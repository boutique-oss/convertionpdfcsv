"""
Interface Gradio — Atelier EBP, page unique à deux onglets :
  - 🧹 Nettoyeur CSV       (core.cleaner — 100 % local, sans IA)
  - ✂️ Zones éditables     (découpage par zone + 1 éditeur par zone, réalignement
                            auto, export ZIP). Pas de mapping de colonnes en amont :
                            les colonnes ne se voient/s'éditent que dans l'éditeur.

Lancer en local : python app_gradio.py
"""
from __future__ import annotations

import csv as _csv
import os
import tempfile
import traceback
import zipfile
from functools import partial
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from core.cleaner import clean_csv
from core.csv_writer import _safe_filename
from core.zone_split import split_into_zones, realign_rows

load_dotenv()

ZONE_PREVIEW_N = 3  # lignes affichées par éditeur de zone (l'export garde tout)


# ── Helpers communs ───────────────────────────────────────────────────────────

def _coerce_rows(data) -> list[list[str]]:
    """Normalise la valeur d'un Dataframe Gradio en liste de listes de str."""
    if data is None:
        return []
    if hasattr(data, "values"):       # pandas DataFrame
        data = data.values.tolist()
    elif hasattr(data, "tolist"):     # numpy array
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
#  ONGLET 2 — Zones éditables (1 éditeur par zone, réalignement auto)
# ══════════════════════════════════════════════════════════════════════════════

def prepare_zones(csv_file):
    """
    Découpe le CSV en zones et réaligne chaque zone à sa largeur la plus logique.
    Retourne (zones, info, edits_reset).
    zones = [{"index", "separateur", "header", "rows"}].
    """
    if csv_file is None:
        return [], "", {}
    try:
        raw_zones, zrap = split_into_zones(csv_file.name)
    except Exception as exc:
        return [], f"❌ Lecture impossible : {exc}", {}
    if not raw_zones:
        return [], "⚠️ Aucune zone détectée dans ce CSV.", {}

    zones = []
    for z in raw_zones:
        header, rows = realign_rows(z["rows"], z["header"])
        zones.append({
            "index": z["index"], "separateur": z["separateur"],
            "header": header, "rows": rows,
        })
    info = (f"{zrap['nb_zones']} zone(s) détectée(s) et réalignée(s). "
            "Édite chaque zone ci-dessous, puis « Exporter ». Tu nommes les fichiers à ta main.")
    return zones, info, {}


def save_zone_edit(index, data, edits):
    """Mémorise les lignes éditées d'une zone (par index) dans l'état edits."""
    edits = dict(edits or {})
    edits[index] = _coerce_rows(data)
    return edits


def run_zone_export(zones, edits, progress=gr.Progress()):
    if not zones:
        return None, "⚠️ Charge d'abord un CSV pour générer les zones."
    edits = edits or {}
    try:
        progress(0.3, desc="Écriture des zones…")
        out_dir = tempfile.mkdtemp(prefix="zones_")
        lignes_rapport = ["═══ EXPORT DES ZONES ═══", f"Zones : {len(zones)}", ""]

        for z in zones:
            # l'éditeur ne montre que les 3 premières lignes : on réinjecte les
            # corrections sur ces lignes, le reste de la zone est exporté tel quel.
            edited = edits.get(z["index"])
            if edited is not None:
                rows = list(edited) + z["rows"][len(edited):]
            else:
                rows = z["rows"]
            label = z["separateur"] or f"ZONE_{z['index']:02d}"
            fname = f"zone_{z['index']:02d}_{_safe_filename(label)}.csv"
            path = os.path.join(out_dir, fname)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.writer(f, delimiter=";", lineterminator="\r\n")
                w.writerow(z["header"])
                w.writerows(rows)
            lignes_rapport.append(f"  • {fname} : {len(rows)} ligne(s)")

        rapport_txt = "\n".join(lignes_rapport)
        rapport_path = os.path.join(out_dir, "rapport.txt")
        Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

        zip_path = os.path.join(out_dir, "zones.zip")
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

        # ══════════ ONGLET 2 : Zones éditables ══════════
        with gr.Tab("✂️ Zones éditables"):
            gr.Markdown(
                "Charge un CSV : il est **découpé par zone** (à chaque séparation / décalage) "
                "et **chaque zone est réalignée** à sa structure la plus logique. Un **éditeur "
                "par zone** s'affiche ci-dessous — tu vois et corriges les colonnes directement "
                "là, pas avant. Puis **Exporter** (1 CSV par zone, à nommer à ta main)."
            )
            zone_csv_in = gr.File(label="CSV à découper", file_types=[".csv", ".tsv", ".txt"])
            zone_info   = gr.Textbox(label="Zones détectées", interactive=False, lines=2)

            zones_state = gr.State([])
            edits_state = gr.State({})

            @gr.render(inputs=[zones_state])
            def render_zone_editors(zones):
                if not zones:
                    gr.Markdown("_⬆️ Charge un CSV : chaque zone détectée s'affichera ici, "
                                "réalignée et éditable._")
                    return
                for z in zones:
                    sep = z["separateur"] or "début de fichier"
                    n = len(z["rows"])
                    apercu = f" — aperçu {min(ZONE_PREVIEW_N, n)} ligne(s) sur {n} (l'export garde tout)" if n > ZONE_PREVIEW_N else ""
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

            btn_zone_export = gr.Button("📦 Exporter les zones (ZIP)", variant="primary", size="lg")
            with gr.Row():
                zone_zip    = gr.File(label="📦 Télécharger les zones (ZIP)")
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
        outputs=[zones_state, zone_info, edits_state],
    )

    btn_zone_export.click(
        fn=run_zone_export,
        inputs=[zones_state, edits_state],
        outputs=[zone_zip, zone_report],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
