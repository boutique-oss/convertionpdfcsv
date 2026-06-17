"""
Interface Gradio — Atelier EBP, page unique à deux onglets :
  - 🧹 Nettoyeur CSV     (core.cleaner — 100 % local, sans IA)
  - 📄 CSV → CSV EBP     (mapping colonnes + analyse sous-familles + export par famille)

L'entrée est toujours un CSV (le PDF a été abandonné).
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
from core.csv_import import (
    read_csv_df, guess_col, csv_to_articles, GUESS, NONE_COL,
)
from core.csv_writer import (
    export_articles_par_famille, COLS_ARTICLES_EBP, _build_article_ebp_row,
)
from core.subfamily import analyse_sous_familles

load_dotenv()


# ── Constantes ────────────────────────────────────────────────────────────────

TVA_CHOICES = [("20 %", 20.0), ("10 %", 10.0), ("5,5 %", 5.5)]
PREVIEW_N   = 10  # nombre de références montrées dans l'aperçu éditable


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
#  ONGLET 2 — CSV → CSV EBP (mapping + sous-familles)
# ══════════════════════════════════════════════════════════════════════════════

def on_csv_uploaded(csv_file):
    """À l'upload : lit les colonnes réelles et pré-remplit les menus de mapping."""
    empty = gr.update(choices=[], value=None)
    empty_opt = gr.update(choices=[NONE_COL], value=NONE_COL)
    if csv_file is None:
        return empty, empty, empty_opt, empty_opt, ""
    try:
        df = read_csv_df(csv_file.name)
    except Exception as exc:
        return empty, empty, empty_opt, empty_opt, f"❌ Lecture impossible : {exc}"

    cols = [str(c) for c in df.columns]
    ref   = guess_col(cols, GUESS["reference"])
    lib   = guess_col(cols, GUESS["libelle"])
    prix  = guess_col(cols, GUESS["prix"])
    unite = guess_col(cols, GUESS["unite"])

    req = lambda v: gr.update(choices=cols, value=v or (cols[0] if cols else None))
    opt = lambda v: gr.update(choices=[NONE_COL] + cols, value=v or NONE_COL)
    info = f"{len(df)} ligne(s), {len(cols)} colonne(s) : {', '.join(cols[:8])}{'…' if len(cols) > 8 else ''}"
    return req(ref), req(lib), opt(prix), opt(unite), info


# ── Aperçu éditable ───────────────────────────────────────────────────────────

def _articles_to_preview(articles, params, n) -> list[list[str]]:
    rows = []
    for a in articles[:n]:
        r = _build_article_ebp_row(
            a, params["fournisseur_code"], params["remise"],
            "TVA20", params["taux_tva"], params["prix_sont_ttc"],
        )
        rows.append([r[c] for c in COLS_ARTICLES_EBP])
    return rows


def _apply_preview_edits(articles, edited_rows):
    """
    Réinjecte les corrections de l'aperçu (libellé, prix HT, unité, famille)
    par appariement sur le « Code article ». Code article reste verrouillé.
    """
    if edited_rows is None:
        return articles
    rows = edited_rows.values.tolist() if hasattr(edited_rows, "values") else list(edited_rows)
    idx = {c: i for i, c in enumerate(COLS_ARTICLES_EBP)}
    by_ref: dict[str, dict] = {}
    for a in articles:
        ref = str(a.get("reference") or "").strip()
        if ref and ref not in by_ref:
            by_ref[ref] = a
    for row in rows:
        if not row or len(row) < len(COLS_ARTICLES_EBP):
            continue
        ref = str(row[idx["Code article"]]).strip()
        a = by_ref.get(ref)
        if a is None:
            continue
        a["nom_dessin"]        = str(row[idx["Libellé"]]).strip()
        a["unite"]             = str(row[idx["Code unité"]]).strip()
        a["code_sous_famille"] = str(row[idx["Code sous-famille article"]]).strip().upper()
        pvht = str(row[idx["PV HT public conseillé"]]).replace(",", ".").strip()
        try:
            if pvht:
                a["prix_conseille"] = float(pvht)
        except ValueError:
            pass
    return articles


# ── Export ────────────────────────────────────────────────────────────────────

def _build_rapport(valides, rapport_familles, params) -> str:
    lines = [
        "═══ RAPPORT CSV → EBP ═══",
        f"Articles    : {len(valides)}",
        f"Fournisseur : {params['fournisseur_code']}",
        f"Remise      : {params['remise']} %",
    ]
    if rapport_familles:
        lines += [
            "",
            "── Analyse des sous-familles ──",
            f"Total classé : {rapport_familles['total']}",
            f"  par règles : {rapport_familles['par_regles']}",
            f"  par LLM    : {rapport_familles['par_llm']}",
            f"  par défaut : {rapport_familles['par_defaut']}",
            "Répartition (1 CSV par famille) :",
        ]
        for fam, n in rapport_familles["repartition"].items():
            lines.append(f"  • articles_{fam}.csv : {n} article(s)")
    return "\n".join(lines)


def _do_csv_export(valides, rapport_familles, params) -> tuple[str, str]:
    out_dir = tempfile.mkdtemp(prefix="ebp_csv_")

    export_articles_par_famille(
        articles=valides,
        output_dir=out_dir,
        fournisseur_code=params["fournisseur_code"],
        remise=params["remise"],
        taux_tva=params["taux_tva"],
        prix_sont_ttc=params["prix_sont_ttc"],
    )

    # Fichier familles articles (codes distincts) — format écran EBP « Familles Articles »
    familles = sorted({a.get("code_sous_famille", "DIVERS") for a in valides})
    fam_path = os.path.join(out_dir, "0_familles_articles.csv")
    with open(fam_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write("Code Famille Articles;Famille Articles\r\n")
        for fam in familles:
            f.write(f"{fam};{fam}\r\n")

    rapport_txt = _build_rapport(valides, rapport_familles, params)
    rapport_path = os.path.join(out_dir, "rapport.txt")
    Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

    zip_path = os.path.join(out_dir, "export_ebp.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fp in Path(out_dir).glob("*.csv"):
            zf.write(fp, fp.name)
        zf.write(rapport_path, "rapport.txt")
    return zip_path, rapport_txt


# ── Callbacks principaux ──────────────────────────────────────────────────────

def run_csv_preview(csv_file, ref_col, lib_col, prix_col, unite_col,
                    fournisseur_code, remise, taux_tva_label, prix_sont_ttc,
                    analyse_familles, classif_llm, progress=gr.Progress()):
    if csv_file is None:
        return None, gr.update(visible=False), "⚠️ Aucun fichier CSV fourni."
    if not ref_col or not lib_col:
        return None, gr.update(visible=False), "⚠️ Choisis au moins les colonnes Référence et Libellé."

    fournisseur_code = (fournisseur_code or "").strip().upper() or "FOUR001"
    params = {
        "fournisseur_code": fournisseur_code,
        "remise":           float(remise),
        "taux_tva":         _resolve_tva(taux_tva_label),
        "prix_sont_ttc":    bool(prix_sont_ttc),
        "analyse_familles": bool(analyse_familles),
    }

    try:
        progress(0.2, desc="Lecture du CSV…")
        df = read_csv_df(csv_file.name)
        articles = csv_to_articles(df, ref_col, lib_col, prix_col, unite_col)
        if not articles:
            return None, gr.update(visible=False), "⚠️ Aucune ligne exploitable dans ce CSV."

        rapport_familles = None
        if analyse_familles:
            progress(0.5, desc="Analyse des sous-familles…")
            client = None
            if classif_llm:
                import anthropic
                try:
                    client = anthropic.Anthropic()
                except Exception:
                    client = None
            articles, rapport_familles = analyse_sous_familles(
                articles, use_llm=classif_llm and client is not None, client=client,
            )

        preview_rows = _articles_to_preview(articles, params, PREVIEW_N)
        state = {"valides": articles, "rapport_familles": rapport_familles, "params": params}
        info = (f"Aperçu de {min(PREVIEW_N, len(articles))} réf. sur {len(articles)}. "
                "Corrige le tableau (famille, libellé, prix HT, unité) puis « Exporter ». "
                "L'unité de chaque ligne est laissée telle quelle.")
        progress(1.0, desc="Aperçu prêt")
        return state, gr.update(value=preview_rows, visible=True), info
    except Exception as exc:
        tb = traceback.format_exc()
        return None, gr.update(visible=False), f"❌ Erreur : {exc}\n\n{tb[-600:]}"


def run_csv_export(state, edited_rows, progress=gr.Progress()):
    if not state:
        return None, "⚠️ Lance d'abord « Analyser & aperçu »."
    try:
        progress(0.3, desc="Application des corrections…")
        valides = _apply_preview_edits(state["valides"], edited_rows)
        params = state["params"]

        rapport_familles = state.get("rapport_familles")
        if params["analyse_familles"]:
            repart: dict[str, int] = {}
            for a in valides:
                fam = a.get("code_sous_famille", "DIVERS")
                repart[fam] = repart.get(fam, 0) + 1
            if rapport_familles:
                rapport_familles = dict(rapport_familles)
                rapport_familles["repartition"] = dict(sorted(repart.items(), key=lambda kv: -kv[1]))

        progress(0.6, desc="Écriture des CSV par famille…")
        zip_path, rapport_txt = _do_csv_export(valides, rapport_familles, params)
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

        # ══════════ ONGLET 2 : CSV → CSV EBP ══════════
        with gr.Tab("📄 CSV → CSV EBP (sous-familles)"):
            gr.Markdown(
                "Charge un CSV fournisseur, **mappe ses colonnes**, et obtiens un export EBP "
                "**1 fichier CSV par famille** (TAPISSERIE · RIDEAU · MOUSSE · SELLERIE). "
                "Les unités sont reprises telles quelles, ligne par ligne."
            )
            with gr.Row():
                with gr.Column(scale=2):
                    csv_in = gr.File(label="CSV fournisseur", file_types=[".csv", ".tsv", ".txt"])
                    map_info = gr.Textbox(label="Colonnes détectées", interactive=False, lines=1)
                    gr.Markdown("**Mapping des colonnes** (rempli automatiquement, ajuste si besoin)")
                    col_ref   = gr.Dropdown(label="Colonne Référence (Code article) *", choices=[])
                    col_lib   = gr.Dropdown(label="Colonne Libellé (sert à classer la famille) *", choices=[])
                    col_prix  = gr.Dropdown(label="Colonne Prix HT conseillé", choices=[NONE_COL], value=NONE_COL)
                    col_unite = gr.Dropdown(label="Colonne Unité (laissée telle quelle)", choices=[NONE_COL], value=NONE_COL)

                with gr.Column(scale=1):
                    gr.Markdown("### 🏭 Paramètres EBP")
                    csv_fourn_code = gr.Textbox(label="Code fournisseur", placeholder="Ex : CAS001")
                    csv_remise     = gr.Slider(0, 80, value=45, step=1, label="Remise fournisseur (%)")
                    csv_prix_ttc   = gr.Checkbox(label="Les prix du CSV sont TTC", value=False)
                    csv_taux_tva   = gr.Radio([l for l, _ in TVA_CHOICES], value="20 %", label="Taux TVA")
                    csv_analyse    = gr.Checkbox(label="Analyse des sous-familles (export par famille)", value=True)
                    csv_classif    = gr.Checkbox(label="Affiner les cas ambigus avec Claude", value=True)

            with gr.Row():
                btn_csv_preview = gr.Button("👁️ Analyser & aperçu (10 réf.)", variant="secondary", size="lg")

            csv_preview_info = gr.Textbox(label="Aperçu", interactive=False, lines=2)
            csv_preview_table = gr.Dataframe(
                headers=COLS_ARTICLES_EBP,
                datatype=["str"] * len(COLS_ARTICLES_EBP),
                col_count=(len(COLS_ARTICLES_EBP), "fixed"),
                type="pandas",
                interactive=True,
                static_columns=[0],  # « Code article » verrouillé (clé d'appariement)
                wrap=True,
                visible=False,
                label="Échantillon — colonnes éditables, sauf « Code article »",
            )
            btn_csv_export = gr.Button("📦 Exporter par famille", variant="primary", size="lg")
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
        outputs=[col_ref, col_lib, col_prix, col_unite, map_info],
    )

    btn_csv_preview.click(
        fn=run_csv_preview,
        inputs=[csv_in, col_ref, col_lib, col_prix, col_unite,
                csv_fourn_code, csv_remise, csv_taux_tva, csv_prix_ttc,
                csv_analyse, csv_classif],
        outputs=[csv_state, csv_preview_table, csv_preview_info],
    )

    btn_csv_export.click(
        fn=run_csv_export,
        inputs=[csv_state, csv_preview_table],
        outputs=[csv_zip, csv_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
