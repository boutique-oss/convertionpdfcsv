"""
Interface Gradio — Atelier EBP, page unique à deux onglets :
  - 🧹 Nettoyeur CSV         (core.cleaner — 100 % local, sans IA)
  - 📄 CSV → EBP par zone    (mapping colonnes + découpage par zone + colonnes EBP
                              + colonne Taux de marge + aperçu éditable + export ZIP)

L'entrée est toujours un CSV. Le regroupement se fait par ZONE (séparateur /
décalage dans le CSV), pas par sous-famille. Les unités sont reprises telles quelles.
Lancer en local : python app_gradio.py
"""
from __future__ import annotations

import os
import tempfile
import traceback
import zipfile
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from core.cleaner import clean_csv
from core.csv_import import read_csv_df, guess_col, zone_rows_to_articles, GUESS, NONE_COL
from core.csv_writer import (
    export_articles_par_zone, build_ebp_row_marge, COLS_ARTICLES_EBP_MARGE,
)
from core.marge import GUESS_PA, GUESS_PV
from core.zone_split import split_into_zones

load_dotenv()


# ── Constantes ────────────────────────────────────────────────────────────────

TVA_CHOICES = [("20 %", 20.0), ("10 %", 10.0), ("5,5 %", 5.5)]
PREVIEW_N   = 10


def _resolve_tva(label: str) -> float:
    for lab, val in TVA_CHOICES:
        if lab == label:
            return val
    return 20.0


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
#  ONGLET 2 — CSV → EBP par zone
# ══════════════════════════════════════════════════════════════════════════════

def on_csv_uploaded(csv_file):
    """À l'upload : lit les colonnes réelles et pré-remplit les menus de mapping."""
    empty = gr.update(choices=[], value=None)
    empty_opt = gr.update(choices=[NONE_COL], value=NONE_COL)
    if csv_file is None:
        return empty, empty, empty_opt, empty_opt, empty_opt, ""
    try:
        df = read_csv_df(csv_file.name)
    except Exception as exc:
        return empty, empty, empty_opt, empty_opt, empty_opt, f"❌ Lecture impossible : {exc}"

    cols = [str(c) for c in df.columns]
    ref   = guess_col(cols, GUESS["reference"])
    lib   = guess_col(cols, GUESS["libelle"])
    pa    = guess_col(cols, GUESS_PA)
    pv    = guess_col(cols, GUESS_PV)
    unite = guess_col(cols, GUESS["unite"])

    req = lambda v: gr.update(choices=cols, value=v or (cols[0] if cols else None))
    opt = lambda v: gr.update(choices=[NONE_COL] + cols, value=v or NONE_COL)
    info = f"{len(df)} ligne(s), {len(cols)} colonne(s) : {', '.join(cols[:8])}{'…' if len(cols) > 8 else ''}"
    return req(ref), req(lib), opt(pa), opt(pv), opt(unite), info


# ── Aperçu éditable ───────────────────────────────────────────────────────────

def _articles_to_preview(articles, params, n) -> list[list[str]]:
    rows = []
    for a in articles[:n]:
        r = build_ebp_row_marge(a, params["fournisseur_code"], params["taux_tva"])
        rows.append([r[c] for c in COLS_ARTICLES_EBP_MARGE])
    return rows


def _apply_preview_edits(articles, edited_rows):
    """
    Réinjecte les corrections de l'aperçu (libellé, PV, PA, unité, zone) par
    appariement sur le « Code article » (verrouillé). Le taux de marge est
    recalculé à l'export depuis PV/PA.
    """
    if edited_rows is None:
        return articles
    rows = edited_rows.values.tolist() if hasattr(edited_rows, "values") else list(edited_rows)
    idx = {c: i for i, c in enumerate(COLS_ARTICLES_EBP_MARGE)}
    by_ref: dict[str, dict] = {}
    for a in articles:
        ref = str(a.get("reference") or "").strip()
        if ref and ref not in by_ref:
            by_ref[ref] = a

    def _num(v):
        s = str(v).replace(",", ".").strip()
        try:
            return float(s) if s else None
        except ValueError:
            return None

    for row in rows:
        if not row or len(row) < len(COLS_ARTICLES_EBP_MARGE):
            continue
        a = by_ref.get(str(row[idx["Code article"]]).strip())
        if a is None:
            continue
        a["nom_dessin"]        = str(row[idx["Libellé"]]).strip()
        a["unite"]             = str(row[idx["Code unité"]]).strip()
        a["code_sous_famille"] = str(row[idx["Code sous-famille article"]]).strip()
        pv = _num(row[idx["PV HT public conseillé"]])
        pa = _num(row[idx["Prix d'achat"]])
        if pv is not None:
            a["prix_conseille"] = pv
        if pa is not None:
            a["prix_achat"] = pa
    return articles


# ── Export ────────────────────────────────────────────────────────────────────

def _build_rapport(articles, params) -> str:
    from collections import defaultdict
    par_zone: dict[str, int] = defaultdict(int)
    for a in articles:
        par_zone[a.get("code_sous_famille", "ZONE")] += 1
    lines = [
        "═══ RAPPORT CSV → EBP (par zone) ═══",
        f"Articles    : {len(articles)}",
        f"Fournisseur : {params['fournisseur_code']}",
        f"Zones       : {len(par_zone)}",
        "",
        "Répartition (1 CSV par zone, renomme à ta main si besoin) :",
    ]
    for i, (zone, n) in enumerate(sorted(par_zone.items()), 1):
        lines.append(f"  • zone_{i:02d}_{zone} : {n} article(s)")
    return "\n".join(lines)


def _do_export(articles, params) -> tuple[str, str]:
    out_dir = tempfile.mkdtemp(prefix="ebp_zone_")
    export_articles_par_zone(
        articles=articles,
        output_dir=out_dir,
        fournisseur_code=params["fournisseur_code"],
        taux_tva=params["taux_tva"],
    )

    # Familles articles (zones distinctes) — format écran EBP « Familles Articles »
    zones = sorted({a.get("code_sous_famille", "ZONE") for a in articles})
    fam_path = os.path.join(out_dir, "0_familles_articles.csv")
    with open(fam_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("Code Famille Articles;Famille Articles\r\n")
        for z in zones:
            f.write(f"{z};{z}\r\n")

    rapport_txt = _build_rapport(articles, params)
    rapport_path = os.path.join(out_dir, "rapport.txt")
    Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

    zip_path = os.path.join(out_dir, "export_ebp.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fp in Path(out_dir).glob("*.csv"):
            zf.write(fp, fp.name)
        zf.write(rapport_path, "rapport.txt")
    return zip_path, rapport_txt


# ── Callbacks principaux ──────────────────────────────────────────────────────

def run_csv_preview(csv_file, ref_col, lib_col, pa_col, pv_col, unite_col,
                    fournisseur_code, taux_tva_label, progress=gr.Progress()):
    if csv_file is None:
        return None, gr.update(visible=False), "⚠️ Aucun fichier CSV fourni."
    if not ref_col or not lib_col:
        return None, gr.update(visible=False), "⚠️ Choisis au moins les colonnes Référence et Libellé."

    params = {
        "fournisseur_code": (fournisseur_code or "").strip().upper() or "FOUR001",
        "taux_tva":         _resolve_tva(taux_tva_label),
    }
    try:
        progress(0.3, desc="Lecture + découpage en zones…")
        zones, zrap = split_into_zones(csv_file.name)
        articles = zone_rows_to_articles(zones, ref_col, lib_col, pa_col, pv_col, unite_col)
        if not articles:
            return None, gr.update(visible=False), "⚠️ Aucune ligne exploitable dans ce CSV."

        preview_rows = _articles_to_preview(articles, params, PREVIEW_N)
        state = {"articles": articles, "params": params}
        info = (f"{len(articles)} article(s) répartis en {zrap['nb_zones']} zone(s). "
                f"Aperçu de {min(PREVIEW_N, len(articles))} réf. — corrige (zone, libellé, "
                "PV, PA, unité) puis « Exporter ». Le taux de marge est recalculé à l'export.")
        progress(1.0, desc="Aperçu prêt")
        return state, gr.update(value=preview_rows, visible=True), info
    except Exception as exc:
        tb = traceback.format_exc()
        return None, gr.update(visible=False), f"❌ Erreur : {exc}\n\n{tb[-600:]}"


def run_csv_export(state, edited_rows, progress=gr.Progress()):
    if not state:
        return None, "⚠️ Lance d'abord « Analyser & aperçu »."
    try:
        progress(0.4, desc="Application des corrections…")
        articles = _apply_preview_edits(state["articles"], edited_rows)
        progress(0.6, desc="Écriture des CSV par zone…")
        zip_path, rapport_txt = _do_export(articles, state["params"])
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

        # ══════════ ONGLET 2 : CSV → EBP par zone ══════════
        with gr.Tab("📄 CSV → EBP par zone"):
            gr.Markdown(
                "Charge un CSV fournisseur, **mappe ses colonnes**. Le fichier est "
                "**découpé par zone** (à chaque séparation / décalage), chaque zone devient "
                "**1 CSV au format EBP** (9 colonnes + **Taux de marge** = (PV−PA)/PV×100). "
                "Aperçu éditable, export ZIP. Unités reprises telles quelles."
            )
            with gr.Row():
                with gr.Column(scale=2):
                    csv_in = gr.File(label="CSV fournisseur", file_types=[".csv", ".tsv", ".txt"])
                    map_info = gr.Textbox(label="Colonnes détectées", interactive=False, lines=1)
                    gr.Markdown("**Mapping des colonnes** (rempli automatiquement, ajuste si besoin)")
                    col_ref   = gr.Dropdown(label="Référence (Code article) *", choices=[])
                    col_lib   = gr.Dropdown(label="Libellé *", choices=[])
                    col_pa    = gr.Dropdown(label="Prix d'achat HT (PA)", choices=[NONE_COL], value=NONE_COL)
                    col_pv    = gr.Dropdown(label="Prix de vente HT (PV)", choices=[NONE_COL], value=NONE_COL)
                    col_unite = gr.Dropdown(label="Unité (laissée telle quelle)", choices=[NONE_COL], value=NONE_COL)

                with gr.Column(scale=1):
                    gr.Markdown("### 🏭 Paramètres EBP")
                    csv_fourn_code = gr.Textbox(label="Code fournisseur", placeholder="Ex : CAS001")
                    csv_taux_tva   = gr.Radio([l for l, _ in TVA_CHOICES], value="20 %",
                                              label="Taux TVA (pour le PV TTC)")

            with gr.Row():
                btn_csv_preview = gr.Button("👁️ Analyser & aperçu (10 réf.)", variant="secondary", size="lg")

            csv_preview_info = gr.Textbox(label="Aperçu", interactive=False, lines=2)
            csv_preview_table = gr.Dataframe(
                headers=COLS_ARTICLES_EBP_MARGE,
                datatype=["str"] * len(COLS_ARTICLES_EBP_MARGE),
                col_count=(len(COLS_ARTICLES_EBP_MARGE), "fixed"),
                type="pandas",
                interactive=True,
                static_columns=[0],  # « Code article » verrouillé (clé d'appariement)
                wrap=True,
                visible=False,
                label="Échantillon — colonnes éditables, sauf « Code article »",
            )
            btn_csv_export = gr.Button("📦 Exporter par zone (ZIP)", variant="primary", size="lg")
            csv_state = gr.State()

            with gr.Row():
                csv_zip    = gr.File(label="📦 Télécharger les CSV EBP (ZIP)")
                csv_report = gr.Textbox(label="📊 Rapport", lines=16, interactive=False)

    # ── Événements ────────────────────────────────────────────────────────────
    btn_clean.click(
        fn=run_clean,
        inputs=[clean_input, opt_drop_empty, opt_drop_dup, opt_trim, opt_headers,
                opt_remove_sym, opt_digits, opt_decimal, opt_ebp],
        outputs=[clean_file_out, clean_report_out],
    )

    csv_in.change(
        fn=on_csv_uploaded,
        inputs=[csv_in],
        outputs=[col_ref, col_lib, col_pa, col_pv, col_unite, map_info],
    )

    btn_csv_preview.click(
        fn=run_csv_preview,
        inputs=[csv_in, col_ref, col_lib, col_pa, col_pv, col_unite,
                csv_fourn_code, csv_taux_tva],
        outputs=[csv_state, csv_preview_table, csv_preview_info],
    )

    btn_csv_export.click(
        fn=run_csv_export,
        inputs=[csv_state, csv_preview_table],
        outputs=[csv_zip, csv_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
