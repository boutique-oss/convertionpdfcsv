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
from core.csv_import import read_csv_df, guess_col, NONE_COL
from core.marge import compute_marges, GUESS_PA, GUESS_PV, COL_MARGE
from core.zone_split import split_into_zones, write_zones

load_dotenv()


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
#  ONGLET 2 — Découpage en zones
# ══════════════════════════════════════════════════════════════════════════════

def _format_zone_report(rapport: dict) -> str:
    sep_lbl = {"\t": "tabulation", ";": ";", ",": ",", "|": "|"}.get(
        rapport.get("delimiteur", ";"), rapport.get("delimiteur", ";"))
    lines = [
        "═══ DÉCOUPAGE EN ZONES ═══",
        f"Séparateur de colonnes : {sep_lbl}",
        f"Lignes lues : {rapport.get('lignes_totales', 0)}",
        f"Zones créées : {rapport.get('nb_zones', 0)}",
        "",
        "Détail (renomme les fichiers à ta main) :",
    ]
    for z in rapport.get("zones", []):
        sep = z["separateur"] or "(début de fichier)"
        lines.append(f"  • zone_{z['index']:02d}.csv  ←  « {sep} »  ({z['lignes']} ligne(s))")
    return "\n".join(lines)


def _zones_to_preview(zones: list[dict]) -> list[list[str]]:
    """Tableau récap des zones détectées : n° · séparateur · nb lignes."""
    return [
        [f"zone_{z['index']:02d}.csv", z["separateur"] or "(début de fichier)", str(len(z["rows"]))]
        for z in zones
    ]


def run_zone_split(csv_file, progress=gr.Progress()):
    if csv_file is None:
        return None, gr.update(value=None, visible=False), "⚠️ Aucun fichier CSV fourni."
    try:
        progress(0.2, desc="Lecture du CSV…")
        zones, rapport = split_into_zones(csv_file.name)
        if not zones:
            return None, gr.update(value=None, visible=False), "⚠️ Aucune zone détectée dans ce CSV."

        progress(0.6, desc="Écriture des zones…")
        out_dir = tempfile.mkdtemp(prefix="zones_")
        files = write_zones(zones, out_dir)

        rapport_txt = _format_zone_report(rapport)
        rapport_path = os.path.join(out_dir, "rapport_zones.txt")
        Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

        zip_path = os.path.join(out_dir, "zones.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, path in files.items():
                zf.write(path, name)
            zf.write(rapport_path, "rapport_zones.txt")

        progress(1.0, desc="Terminé !")
        recap = _zones_to_preview(zones)
        return zip_path, gr.update(value=recap, visible=True), rapport_txt
    except Exception as exc:
        tb = traceback.format_exc()
        return None, gr.update(value=None, visible=False), f"❌ Erreur : {exc}\n\n{tb[-600:]}"


# ══════════════════════════════════════════════════════════════════════════════
#  ONGLET 3 — Taux de marge
# ══════════════════════════════════════════════════════════════════════════════

def on_marge_uploaded(csv_file):
    """À l'upload : pré-remplit les menus prix d'achat / prix de vente."""
    empty = gr.update(choices=[], value=None)
    if csv_file is None:
        return empty, empty, ""
    try:
        df = read_csv_df(csv_file.name)
    except Exception as exc:
        return empty, empty, f"❌ Lecture impossible : {exc}"
    cols = [str(c) for c in df.columns]
    pa = guess_col(cols, GUESS_PA)
    pv = guess_col(cols, GUESS_PV)
    mk = lambda v: gr.update(choices=cols, value=v or (cols[0] if cols else None))
    info = f"{len(df)} ligne(s), {len(cols)} colonne(s) : {', '.join(cols[:8])}{'…' if len(cols) > 8 else ''}"
    return mk(pa), mk(pv), info


def _format_marge_report(rapport: dict) -> str:
    moy = rapport["moyenne"]
    lines = [
        "═══ TAUX DE MARGE ═══",
        "Formule : (PV − PA) / PV × 100",
        f"Lignes traitées : {rapport['lignes']}",
        f"Marges calculées : {rapport['calculees']}",
        f"Moyenne : {moy:.2f} %".replace(".", ",") if moy is not None else "Moyenne : —",
        f"Min / Max : {rapport['mini']} % / {rapport['maxi']} %",
    ]
    if rapport["pv_nul"]:
        lines.append(f"⚠️ {rapport['pv_nul']} ligne(s) sans prix de vente (marge vide).")
    if rapport["marge_negative"]:
        lines.append(f"⚠️ {rapport['marge_negative']} ligne(s) à marge NÉGATIVE (PV < PA).")
    return "\n".join(lines)


def run_marge(csv_file, pa_col, pv_col):
    if csv_file is None:
        return None, "⚠️ Aucun fichier CSV fourni."
    if not pa_col or not pv_col:
        return None, "⚠️ Choisis la colonne prix d'achat ET la colonne prix de vente."
    if pa_col == pv_col:
        return None, "⚠️ Prix d'achat et prix de vente doivent être deux colonnes différentes."
    try:
        df = read_csv_df(csv_file.name)
        out_df, rapport = compute_marges(df, pa_col, pv_col)
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-500:]}"

    base = Path(csv_file.name).stem or "fichier"
    out_dir = tempfile.mkdtemp(prefix="marge_")
    out_path = os.path.join(out_dir, f"{base}_marge.csv")
    out_df.to_csv(out_path, sep=";", index=False, encoding="utf-8-sig", lineterminator="\r\n")
    return out_path, _format_marge_report(rapport)


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

        # ══════════ ONGLET 2 : Découpage en zones ══════════
        with gr.Tab("✂️ Découper en zones"):
            gr.Markdown(
                "Charge un CSV : à **chaque séparation** (ligne vide) ou **décalage de "
                "données** (titre de section qui change la structure des colonnes), le "
                "fichier est **coupé** et tout ce qui suit part dans un nouveau CSV. "
                "Les fichiers sortent numérotés (`zone_01.csv`…) — tu leur donnes "
                "l'intitulé toi-même. Les valeurs sont reprises telles quelles."
            )
            with gr.Row():
                with gr.Column(scale=1):
                    zone_in = gr.File(label="CSV à découper", file_types=[".csv", ".tsv", ".txt"])
                    btn_zone = gr.Button("✂️ Découper en zones", variant="primary", size="lg")
                    zone_zip = gr.File(label="📦 Télécharger les zones (ZIP)")
                with gr.Column(scale=2):
                    zone_table = gr.Dataframe(
                        headers=["Fichier", "Séparateur détecté", "Lignes"],
                        datatype=["str", "str", "str"],
                        col_count=(3, "fixed"),
                        interactive=False,
                        wrap=True,
                        visible=False,
                        label="Zones détectées",
                    )
                    zone_report = gr.Textbox(label="📊 Rapport de découpage", lines=14, interactive=False)

        # ══════════ ONGLET 3 : Taux de marge ══════════
        with gr.Tab("📈 Taux de marge"):
            gr.Markdown(
                "Charge un CSV avec un **prix d'achat** et un **prix de vente** (HT). "
                "Je calcule le **taux de marge = (PV − PA) / PV × 100** ligne par ligne "
                "et j'ajoute la colonne « Taux de marge ». Aucune remise à saisir."
            )
            with gr.Row():
                with gr.Column(scale=1):
                    marge_in = gr.File(label="CSV (prix achat + prix vente)",
                                       file_types=[".csv", ".tsv", ".txt"])
                    marge_info = gr.Textbox(label="Colonnes détectées", interactive=False, lines=1)
                    marge_pa = gr.Dropdown(label="Colonne Prix d'achat (PA) *", choices=[])
                    marge_pv = gr.Dropdown(label="Colonne Prix de vente (PV) *", choices=[])
                    btn_marge = gr.Button("📈 Calculer le taux de marge", variant="primary", size="lg")
                with gr.Column(scale=1):
                    marge_out    = gr.File(label="📥 CSV avec colonne « Taux de marge »")
                    marge_report = gr.Textbox(label="📊 Rapport", lines=12, interactive=False)

    # ── Événements ────────────────────────────────────────────────────────────
    btn_clean.click(
        fn=run_clean,
        inputs=[clean_input, opt_drop_empty, opt_drop_dup, opt_trim, opt_headers,
                opt_remove_sym, opt_digits, opt_decimal, opt_ebp],
        outputs=[clean_file_out, clean_report_out],
    )

    btn_zone.click(
        fn=run_zone_split,
        inputs=[zone_in],
        outputs=[zone_zip, zone_table, zone_report],
    )

    marge_in.change(
        fn=on_marge_uploaded,
        inputs=[marge_in],
        outputs=[marge_pa, marge_pv, marge_info],
    )

    btn_marge.click(
        fn=run_marge,
        inputs=[marge_in, marge_pa, marge_pv],
        outputs=[marge_out, marge_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
