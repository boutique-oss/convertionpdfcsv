"""
Interface Gradio — Atelier EBP, page unique à deux onglets :
  - 🧹 Nettoyeur CSV  (core.cleaner — 100 % local, sans IA)
  - 📄 CSV → EBP      (mapping colonnes → EBP + aperçu éditable + export 1 CSV)

Entrée = un CSV. Sortie = UN seul CSV EBP (9 colonnes + Taux de marge). Pas de
découpage en zones. Les unités sont reprises telles quelles.
Lancer en local : python app_gradio.py
"""
from __future__ import annotations

import csv as _csv
import os
import tempfile
import traceback
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from core.cleaner import clean_csv
from core.csv_import import read_csv_df, df_to_articles, guess_col, GUESS, NONE_COL
from core.csv_writer import build_ebp_row_marge, COLS_ARTICLES_EBP_MARGE
from core.marge import GUESS_PA, GUESS_PV

load_dotenv()

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
#  ONGLET 2 — CSV → EBP (un seul fichier)
# ══════════════════════════════════════════════════════════════════════════════

def on_csv_uploaded(csv_file):
    """À l'upload : pré-remplit les menus de mapping depuis les colonnes réelles."""
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


def run_preview(csv_file, ref_col, lib_col, pa_col, pv_col, unite_col,
                fournisseur_code, taux_tva_label, progress=gr.Progress()):
    if csv_file is None:
        return gr.update(visible=False), "⚠️ Aucun fichier CSV fourni."
    if not ref_col or not lib_col:
        return gr.update(visible=False), "⚠️ Choisis au moins les colonnes Référence et Libellé."

    fournisseur_code = (fournisseur_code or "").strip().upper() or "FOUR001"
    taux_tva = _resolve_tva(taux_tva_label)
    try:
        progress(0.4, desc="Lecture + mapping EBP…")
        df = read_csv_df(csv_file.name)
        articles = df_to_articles(df, ref_col, lib_col, pa_col, pv_col, unite_col)
        if not articles:
            return gr.update(visible=False), "⚠️ Aucune ligne exploitable dans ce CSV."
        rows = [
            [build_ebp_row_marge(a, fournisseur_code, taux_tva)[c] for c in COLS_ARTICLES_EBP_MARGE]
            for a in articles
        ]
        progress(1.0, desc="Aperçu prêt")
        info = (f"{len(rows)} ligne(s) converties au format EBP. Édite le tableau si besoin, "
                "puis « Exporter ». Le « Code sous-famille article » est laissé vide (à toi de jouer).")
        return gr.update(value=rows, visible=True), info
    except Exception as exc:
        tb = traceback.format_exc()
        return gr.update(visible=False), f"❌ Erreur : {exc}\n\n{tb[-600:]}"


def run_export(edited_table, progress=gr.Progress()):
    rows = _coerce_rows(edited_table)
    if not rows:
        return None, "⚠️ Lance d'abord « Analyser & aperçu »."
    try:
        progress(0.5, desc="Écriture du CSV EBP…")
        out_dir = tempfile.mkdtemp(prefix="ebp_")
        out_path = os.path.join(out_dir, "articles_ebp.csv")
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f, delimiter=";", lineterminator="\r\n")
            w.writerow(COLS_ARTICLES_EBP_MARGE)
            w.writerows(rows)
        progress(1.0, desc="Terminé !")
        return out_path, f"✅ {len(rows)} ligne(s) exportées → articles_ebp.csv"
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-500:]}"


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

        # ══════════ ONGLET 2 : CSV → EBP ══════════
        with gr.Tab("📄 CSV → EBP"):
            gr.Markdown(
                "Charge un CSV, **mappe ses colonnes → EBP**, vérifie/édite l'aperçu, "
                "puis exporte **un seul CSV EBP** (9 colonnes + **Taux de marge** = "
                "(PV−PA)/PV). Unités reprises telles quelles."
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

            btn_preview = gr.Button("👁️ Analyser & aperçu", variant="secondary", size="lg")
            preview_info = gr.Textbox(label="Aperçu", interactive=False, lines=2)
            preview_table = gr.Dataframe(
                headers=COLS_ARTICLES_EBP_MARGE,
                datatype=["str"] * len(COLS_ARTICLES_EBP_MARGE),
                col_count=(len(COLS_ARTICLES_EBP_MARGE), "fixed"),
                type="array",
                interactive=True,
                wrap=True,
                visible=False,
                label="Aperçu EBP — éditable",
            )
            btn_export = gr.Button("📦 Exporter (1 CSV EBP)", variant="primary", size="lg")
            with gr.Row():
                ebp_file   = gr.File(label="📥 CSV EBP")
                ebp_report = gr.Textbox(label="📊 Rapport", lines=4, interactive=False)

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

    btn_preview.click(
        fn=run_preview,
        inputs=[csv_in, col_ref, col_lib, col_pa, col_pv, col_unite,
                csv_fourn_code, csv_taux_tva],
        outputs=[preview_table, preview_info],
    )

    btn_export.click(
        fn=run_export,
        inputs=[preview_table],
        outputs=[ebp_file, ebp_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
