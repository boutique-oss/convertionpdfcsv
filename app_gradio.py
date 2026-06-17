"""
Interface Gradio — Atelier EBP, page unique à deux onglets :
  - 🧹 Nettoyeur CSV     (core.cleaner — 100 % local, sans IA)
  - 📄 CSV → EBP par zone

Onglet 2, pipeline complet :
  1. découpage par ZONE (séparation / décalage) + réalignement auto
  2. 1 éditeur par zone (aperçu 3 lignes) pour corriger les données
  3. mapping colonnes → champs EBP, affiché APRÈS l'upload (sous les éditeurs)
  4. export 1 CSV EBP par zone (9 colonnes + Taux de marge), ZIP

Lancer en local : python app_gradio.py
"""
from __future__ import annotations

import os
import tempfile
import traceback
import zipfile
from functools import partial
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from core.cleaner import clean_csv
from core.csv_import import zone_rows_to_articles, guess_col, GUESS, NONE_COL
from core.csv_writer import export_articles_par_zone, COLS_ARTICLES_EBP_MARGE
from core.marge import GUESS_PA, GUESS_PV
from core.zone_split import split_into_zones, realign_rows

load_dotenv()

ZONE_PREVIEW_N = 3  # lignes affichées par éditeur de zone (l'export garde tout)
TVA_CHOICES = [("20 %", 20.0), ("10 %", 10.0), ("5,5 %", 5.5)]


def _resolve_tva(label: str) -> float:
    for lab, val in TVA_CHOICES:
        if lab == label:
            return val
    return 20.0


def _coerce_rows(data) -> list[list[str]]:
    if data is None:
        return []
    if hasattr(data, "values"):
        data = data.values.tolist()
    elif hasattr(data, "tolist"):
        data = data.tolist()
    return [[("" if c is None else str(c)) for c in row] for row in data]


# ══════════════════════════════════════════════════════════════════════════════
#  ONGLET 1 — Nettoyeur CSV
# ══════════════════════════════════════════════════════════════════════════════

def _format_clean_report(report: dict) -> str:
    lines = [
        "═══ RAPPORT DE NETTOYAGE ═══",
        f"Encodage détecté   : {report.get('encodage_detecte')}",
        f"Séparateur détecté : {report.get('separateur_detecte')}",
        f"Lignes   : {report.get('lignes_initiales')} → {report.get('lignes_finales')}",
        f"Colonnes : {report.get('colonnes_initiales')} → {report.get('colonnes_finales')}",
        f"Sortie   : séparateur '{report.get('separateur_sortie')}' / {report.get('encodage_sortie')}",
        "",
        "Actions :",
    ]
    for a in report.get("actions", []):
        lines.append(f"  • {a}")
    return "\n".join(lines)


def run_clean(csv_file, drop_empty, drop_duplicates, trim_whitespace,
              normalize_headers, remove_symbols, keep_digits_only,
              decimal_comma, ebp_format):
    if csv_file is None:
        return None, "⚠️ Aucun fichier CSV fourni."
    try:
        raw = Path(csv_file.name).read_bytes()
    except Exception as exc:
        return None, f"❌ Lecture impossible : {exc}"
    if not raw.strip():
        return None, "⚠️ Le fichier est vide."

    options = {
        "drop_empty": drop_empty, "drop_duplicates": drop_duplicates,
        "trim_whitespace": trim_whitespace, "normalize_headers": normalize_headers,
        "remove_symbols": remove_symbols, "keep_digits_only": keep_digits_only,
        "decimal_comma": decimal_comma, "ebp_format": ebp_format,
    }
    try:
        cleaned, report = clean_csv(raw, options)
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-500:]}"

    base = Path(csv_file.name).stem or "fichier"
    out_dir = tempfile.mkdtemp(prefix="csv_clean_")
    out_path = os.path.join(out_dir, f"{base}_nettoye.csv")
    Path(out_path).write_bytes(cleaned)
    return out_path, _format_clean_report(report)


# ══════════════════════════════════════════════════════════════════════════════
#  ONGLET 2 — Zones éditables + mapping EBP
# ══════════════════════════════════════════════════════════════════════════════

def prepare_zones(csv_file):
    """
    Découpe + réaligne les zones, et pré-remplit les menus de mapping (affichés
    sous les éditeurs, après l'upload).

    Retourne : zones, info, edits({}), upd_ref, upd_lib, upd_pa, upd_pv, upd_unite.
    """
    empty = gr.update(choices=[], value=None)
    empty_opt = gr.update(choices=[NONE_COL], value=NONE_COL)
    if csv_file is None:
        return [], "", {}, empty, empty, empty_opt, empty_opt, empty_opt
    try:
        raw_zones, zrap = split_into_zones(csv_file.name)
    except Exception as exc:
        return [], f"❌ Lecture impossible : {exc}", {}, empty, empty, empty_opt, empty_opt, empty_opt
    if not raw_zones:
        return [], "⚠️ Aucune zone détectée dans ce CSV.", {}, empty, empty, empty_opt, empty_opt, empty_opt

    zones = []
    for z in raw_zones:
        header, rows = realign_rows(z["rows"], z["header"])
        zones.append({"index": z["index"], "separateur": z["separateur"],
                      "header": header, "rows": rows})

    cols = [str(c) for c in zrap.get("header", [])] or list(zones[0]["header"])
    ref   = guess_col(cols, GUESS["reference"])
    lib   = guess_col(cols, GUESS["libelle"])
    pa    = guess_col(cols, GUESS_PA)
    pv    = guess_col(cols, GUESS_PV)
    unite = guess_col(cols, GUESS["unite"])
    req = lambda v: gr.update(choices=cols, value=v or (cols[0] if cols else None))
    opt = lambda v: gr.update(choices=[NONE_COL] + cols, value=v or NONE_COL)

    info = (f"{zrap['nb_zones']} zone(s) détectée(s) et réalignée(s). Corrige chaque zone "
            "ci-dessous, vérifie le mapping EBP, puis « Exporter ».")
    return zones, info, {}, req(ref), req(lib), opt(pa), opt(pv), opt(unite)


def save_zone_edit(index, data, edits):
    edits = dict(edits or {})
    edits[index] = _coerce_rows(data)
    return edits


def _build_rapport(articles, params) -> str:
    from collections import defaultdict
    par_zone: dict[str, int] = defaultdict(int)
    for a in articles:
        par_zone[a.get("code_sous_famille", "ZONE")] += 1
    lines = [
        "═══ EXPORT CSV → EBP (par zone) ═══",
        f"Articles    : {len(articles)}",
        f"Fournisseur : {params['fournisseur_code']}",
        f"Zones       : {len(par_zone)}",
        "",
        "1 CSV EBP par zone (9 colonnes + Taux de marge), à renommer à ta main :",
    ]
    for i, (zone, n) in enumerate(sorted(par_zone.items()), 1):
        lines.append(f"  • zone_{i:02d}_{zone} : {n} article(s)")
    return "\n".join(lines)


def run_export(zones, edits, ref_col, lib_col, pa_col, pv_col, unite_col,
               fournisseur_code, taux_tva_label, progress=gr.Progress()):
    if not zones:
        return None, "⚠️ Charge d'abord un CSV pour générer les zones."
    if not ref_col or not lib_col:
        return None, "⚠️ Mappe au moins les colonnes Référence et Libellé."

    params = {
        "fournisseur_code": (fournisseur_code or "").strip().upper() or "FOUR001",
        "taux_tva":         _resolve_tva(taux_tva_label),
    }
    edits = edits or {}
    try:
        progress(0.3, desc="Application des corrections + mapping EBP…")
        all_articles: list[dict] = []
        for z in zones:
            edited = edits.get(z["index"])
            rows = (list(edited) + z["rows"][len(edited):]) if edited is not None else z["rows"]
            ztmp = {"index": z["index"], "separateur": z["separateur"],
                    "header": z["header"], "rows": rows}
            all_articles += zone_rows_to_articles(
                [ztmp], ref_col, lib_col, pa_col, pv_col, unite_col)

        if not all_articles:
            return None, "⚠️ Aucune ligne exploitable après mapping."

        progress(0.6, desc="Écriture des CSV EBP par zone…")
        out_dir = tempfile.mkdtemp(prefix="ebp_zone_")
        export_articles_par_zone(all_articles, out_dir,
                                 fournisseur_code=params["fournisseur_code"],
                                 taux_tva=params["taux_tva"])

        # Familles articles (zones distinctes)
        zlbl = sorted({a.get("code_sous_famille", "ZONE") for a in all_articles})
        fam_path = os.path.join(out_dir, "0_familles_articles.csv")
        with open(fam_path, "w", newline="", encoding="utf-8-sig") as f:
            f.write("Code Famille Articles;Famille Articles\r\n")
            for z in zlbl:
                f.write(f"{z};{z}\r\n")

        rapport_txt = _build_rapport(all_articles, params)
        rapport_path = os.path.join(out_dir, "rapport.txt")
        Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

        zip_path = os.path.join(out_dir, "export_ebp.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for fp in Path(out_dir).glob("*.csv"):
                zf.write(fp, fp.name)
            zf.write(rapport_path, "rapport.txt")

        progress(1.0, desc="Terminé !")
        return zip_path, rapport_txt
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-600:]}"


# ══════════════════════════════════════════════════════════════════════════════
#  Interface
# ══════════════════════════════════════════════════════════════════════════════

with gr.Blocks(title="Atelier — Outils EBP", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🧰 Atelier — Outils CSV pour EBP Gestion Commerciale")

    with gr.Tabs():

        # ══════════ ONGLET 1 : Nettoyeur CSV ══════════
        with gr.Tab("🧹 Nettoyeur CSV"):
            gr.Markdown("Charge un CSV, choisis le nettoyage, récupère un fichier propre. "
                        "100 % local, sans IA ni clé API.")
            with gr.Row():
                with gr.Column(scale=2):
                    clean_input = gr.File(label="Fichier CSV / TSV / TXT",
                                          file_types=[".csv", ".tsv", ".txt"])
                    with gr.Row():
                        opt_drop_empty = gr.Checkbox(label="Supprimer lignes/colonnes vides", value=True)
                        opt_drop_dup   = gr.Checkbox(label="Supprimer les doublons", value=True)
                    with gr.Row():
                        opt_trim    = gr.Checkbox(label="Espaces superflus", value=True)
                        opt_headers = gr.Checkbox(label="Normaliser les en-têtes", value=True)
                    with gr.Row():
                        opt_remove_sym = gr.Checkbox(label="Retirer les symboles (€ % # *…)", value=False)
                        opt_digits     = gr.Checkbox(label="Garder uniquement les chiffres", value=False)
                    with gr.Row():
                        opt_decimal = gr.Checkbox(label="Décimales en virgule FR", value=False)
                        opt_ebp     = gr.Checkbox(label="Format EBP ( ; / utf-8-sig )", value=False)
                    btn_clean = gr.Button("🧹 Nettoyer", variant="primary", size="lg")
                with gr.Column(scale=2):
                    clean_file_out   = gr.File(label="📥 CSV nettoyé")
                    clean_report_out = gr.Textbox(label="📊 Rapport de nettoyage",
                                                  lines=16, interactive=False)

        # États partagés entre l'onglet Analyse et l'onglet Conversion
        zones_state = gr.State([])
        edits_state = gr.State({})

        # ══════════ ONGLET 2 : ANALYSE (avant conversion) ══════════
        with gr.Tab("🔎 Analyse"):
            gr.Markdown(
                "**Étape 1 — Analyse.** Charge un CSV : il est **découpé par zone** "
                "(séparation / décalage) et **chaque zone est réalignée**. Corrige chaque "
                "zone dans son éditeur (aperçu 3 lignes) et **mappe les colonnes → EBP** "
                "(affiché après l'upload). Passe ensuite à l'onglet **Conversion EBP**."
            )
            zone_csv_in = gr.File(label="CSV à analyser", file_types=[".csv", ".tsv", ".txt"])
            zone_info   = gr.Textbox(label="Zones détectées", interactive=False, lines=2)

            gr.Markdown("### ✏️ Éditeurs de zone")

            @gr.render(inputs=[zones_state])
            def render_zone_editors(zones):
                if not zones:
                    gr.Markdown("_⬆️ Charge un CSV : chaque zone détectée s'affichera ici, "
                                "réalignée et éditable (3 lignes)._")
                    return
                for z in zones:
                    sep = z["separateur"] or "début de fichier"
                    n = len(z["rows"])
                    apercu = (f" — aperçu {min(ZONE_PREVIEW_N, n)}/{n} (l'export garde tout)"
                              if n > ZONE_PREVIEW_N else "")
                    gr.Markdown(f"#### ✂️ Zone {z['index']:02d} — « {sep} » · {n} ligne(s){apercu}")
                    tbl = gr.Dataframe(
                        value=z["rows"][:ZONE_PREVIEW_N],
                        headers=z["header"],
                        col_count=(len(z["header"]), "fixed"),
                        type="array",
                        interactive=True,
                        wrap=True,
                    )
                    tbl.change(
                        fn=partial(save_zone_edit, z["index"]),
                        inputs=[tbl, edits_state],
                        outputs=[edits_state],
                    )

            gr.Markdown("### 🔗 Mapping colonnes → EBP (rempli après l'upload)")
            with gr.Row():
                col_ref   = gr.Dropdown(label="Référence (Code article) *", choices=[])
                col_lib   = gr.Dropdown(label="Libellé *", choices=[])
            with gr.Row():
                col_pa    = gr.Dropdown(label="Prix d'achat HT (PA)", choices=[NONE_COL], value=NONE_COL)
                col_pv    = gr.Dropdown(label="Prix de vente HT (PV)", choices=[NONE_COL], value=NONE_COL)
                col_unite = gr.Dropdown(label="Unité (laissée telle quelle)", choices=[NONE_COL], value=NONE_COL)

        # ══════════ ONGLET 3 : CONVERSION EBP (après analyse) ══════════
        with gr.Tab("📦 Conversion EBP"):
            gr.Markdown(
                "**Étape 2 — Conversion.** Reprend l'analyse de l'onglet précédent (zones, "
                "corrections, mapping) et produit **1 CSV EBP par zone** (9 colonnes + "
                "**Taux de marge** = (PV−PA)/PV), plus `0_familles_articles.csv`, en ZIP."
            )
            with gr.Row():
                csv_fourn_code = gr.Textbox(label="Code fournisseur", placeholder="Ex : CAS001")
                csv_taux_tva   = gr.Radio([l for l, _ in TVA_CHOICES], value="20 %",
                                          label="Taux TVA (pour le PV TTC)")
            btn_zone_export = gr.Button("📦 Convertir & exporter (ZIP)", variant="primary", size="lg")
            with gr.Row():
                zone_zip    = gr.File(label="📦 Télécharger les CSV EBP (ZIP)")
                zone_report = gr.Textbox(label="📊 Rapport d'export", lines=12, interactive=False)

    # ── Événements ────────────────────────────────────────────────────────────
    btn_clean.click(
        fn=run_clean,
        inputs=[clean_input, opt_drop_empty, opt_drop_dup, opt_trim, opt_headers,
                opt_remove_sym, opt_digits, opt_decimal, opt_ebp],
        outputs=[clean_file_out, clean_report_out],
    )

    zone_csv_in.change(
        fn=prepare_zones,
        inputs=[zone_csv_in],
        outputs=[zones_state, zone_info, edits_state,
                 col_ref, col_lib, col_pa, col_pv, col_unite],
    )

    btn_zone_export.click(
        fn=run_export,
        inputs=[zones_state, edits_state, col_ref, col_lib, col_pa, col_pv, col_unite,
                csv_fourn_code, csv_taux_tva],
        outputs=[zone_zip, zone_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
