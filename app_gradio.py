"""
Interface Gradio — Conversion catalogue PDF → CSV EBP Gestion Commerciale.
Lancer : python app_gradio.py
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import traceback
import zipfile
from collections import defaultdict
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from core.agent import extract_articles
from core.csv_writer import export_ebp
from core.extractor import debug_layout
from core.profil import list_profiles, load_profile, save_profile

load_dotenv()


# ── Constantes ────────────────────────────────────────────────────────────────

TVA_CHOICES   = [("20 %", 20.0), ("10 %", 10.0), ("5,5 %", 5.5)]
UNITE_CHOICES = ["ml", "M", "U", "M2", "H", "KG", "L", "RL", "PCE", "Pièce"]


# ── Fonction principale ───────────────────────────────────────────────────────

def run_debug_layout(pdf_file, page_from: int, page_to: int) -> str:
    if pdf_file is None:
        return "⚠️ Aucun fichier PDF fourni."
    try:
        pages = debug_layout(pdf_file.name, page_from, page_to)
        if not pages:
            return "Impossible d'analyser (pdfplumber manquant ?)."
        lines = ["Page | Tables | Lignes texte | Prix trouvés"]
        lines.append("-----|--------|-------------|-------------")
        vides = []
        for p in pages:
            marker = " ← vide" if p["prix_trouves"] == 0 and p["lignes_texte"] == 0 else ""
            lines.append(
                f"  {p['page']:3d} |    {p['tables']:3d} |         {p['lignes_texte']:3d} |          {p['prix_trouves']:3d}{marker}"
            )
            if p["prix_trouves"] == 0:
                vides.append(str(p["page"]))
        if vides:
            lines.append(f"\n⚠️ Pages sans prix détecté : {', '.join(vides)}")
        return "\n".join(lines)
    except Exception as exc:
        return f"❌ Erreur : {exc}"


def run_conversion(
    pdf_file,
    page_from: int,
    page_to: int,
    fournisseur_nom: str,
    fournisseur_code: str,
    code_famille: str,
    remise: float,
    prix_sont_ttc: bool,
    taux_tva_label: str,
    unite_defaut: str,
    sample_mode: bool,
    profil_nom_save: str,
    collection_famille_map_json: str,
    regex_config_json: str,
    progress=gr.Progress(),
):
    if pdf_file is None:
        return None, "⚠️ Aucun fichier PDF fourni.", "", ""

    fournisseur_nom  = fournisseur_nom.strip()
    fournisseur_code = fournisseur_code.strip().upper()

    if not fournisseur_nom:
        return None, "⚠️ Le nom du fournisseur est obligatoire.", "", ""
    if not fournisseur_code:
        fournisseur_code = fournisseur_nom[:8].upper().replace(" ", "")

    # Résolution taux TVA
    taux_tva = 20.0
    for label, val in TVA_CHOICES:
        if label == taux_tva_label:
            taux_tva = val
            break

    # Parse JSON optionnels
    collection_famille_map: dict | None = None
    if collection_famille_map_json.strip():
        try:
            collection_famille_map = json.loads(collection_famille_map_json)
        except json.JSONDecodeError:
            return None, "⚠️ JSON du mapping collection invalide.", "", ""

    regex_config: dict | None = None
    if regex_config_json.strip():
        try:
            regex_config = json.loads(regex_config_json)
        except json.JSONDecodeError:
            return None, "⚠️ JSON de l'extracteur regex invalide.", "", ""

    # Sauvegarde du profil si demandée
    if profil_nom_save.strip():
        profil_data: dict = {
            "fournisseur_nom":         fournisseur_nom,
            "fournisseur_code":        fournisseur_code,
            "code_famille":            code_famille,
            "remise":                  remise,
            "prix_sont_ttc":           prix_sont_ttc,
            "taux_tva_label":          taux_tva_label,
            "unite_defaut":            unite_defaut,
        }
        if collection_famille_map:
            profil_data["collection_famille_map"] = collection_famille_map
        if regex_config:
            profil_data["regex_config"] = regex_config
        save_profile(profil_nom_save.strip(), profil_data)

    def _progress_cb(done, total):
        progress(done / max(total, 1), desc=f"Batch {done}/{total}")

    try:
        progress(0, desc="Extraction en cours…")
        valides, anomalies, rapport = extract_articles(
            pdf_path=pdf_file.name,
            page_from=page_from,
            page_to=page_to,
            password="",
            default_unit=unite_defaut,
            progress_cb=_progress_cb,
            sample_mode=sample_mode,
            regex_config=regex_config,
        )

        if not valides and not anomalies:
            return None, "⚠️ Aucun article détecté dans ces pages.", "", ""

        # Export CSV
        progress(0.9, desc="Écriture des fichiers CSV…")
        out_dir = tempfile.mkdtemp(prefix="ebp_export_")
        export_ebp(
            articles=valides,
            output_dir=out_dir,
            fournisseur_nom=fournisseur_nom,
            fournisseur_code=fournisseur_code,
            remise=remise,
            unite_defaut=unite_defaut,
            code_famille=code_famille,
            prix_sont_ttc=prix_sont_ttc,
            taux_tva=taux_tva,
            collection_famille_map=collection_famille_map,
        )

        # Rapport d'audit
        rapport_txt = _build_rapport(rapport, valides, anomalies, sample_mode)
        rapport_path = os.path.join(out_dir, "rapport_conversion.txt")
        Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

        # Anomalies CSV
        if anomalies:
            anomalies_path = os.path.join(out_dir, "anomalies.csv")
            _write_anomalies(anomalies, anomalies_path)

        # ZIP de sortie
        zip_path = os.path.join(out_dir, "export_ebp.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in Path(out_dir).glob("*.csv"):
                zf.write(f, f.name)
            zf.write(rapport_path, "rapport_conversion.txt")

        progress(1.0, desc="Terminé !")
        return zip_path, rapport_txt, _anomalies_preview(anomalies), ""

    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-800:]}", "", ""


def _build_rapport(rapport: dict, valides, anomalies, sample_mode: bool) -> str:
    mode_extract = rapport.get("mode", "text")
    lines = [
        "═══ RAPPORT DE CONVERSION ═══",
        f"Mode         : {'ÉCHANTILLON' if sample_mode else 'COMPLET'} ({mode_extract})",
        f"Lignes lues  : {rapport['lignes_lues']}",
        f"Prix source  : {rapport['prix_source_tokens']} tokens trouvés dans le texte",
        f"Articles OK  : {rapport['articles_ecrits']}",
        f"Anomalies    : {rapport['anomalies']}",
        f"Couverture   : {rapport['taux_couverture_pct']} %",
        "",
        "Fichiers produits (à importer dans cet ordre) :",
        "  1. 1_familles_articles.csv",
        "  2. 2_fournisseur.csv",
        "  3. 3_articles.csv",
    ]

    # Top 10 prix les plus élevés
    avec_prix = [a for a in valides if a.get("prix_conseille", 0) > 0]
    if avec_prix:
        top10 = sorted(avec_prix, key=lambda a: a["prix_conseille"], reverse=True)[:10]
        lines += ["", "── Top 10 prix conseillés ──"]
        for a in top10:
            ref  = a.get("reference", "?")
            nom  = str(a.get("nom_dessin", ""))[:30]
            prix = f"{a['prix_conseille']:.2f}".replace(".", ",")
            lines.append(f"  {prix} €  [{ref}] {nom}")

    # Doublons (même référence, prix différents)
    ref_prix: dict[str, list] = defaultdict(list)
    for a in valides:
        ref = a.get("reference", "")
        if ref:
            ref_prix[ref].append(a.get("prix_conseille", 0))
    doublons = {ref: prix for ref, prix in ref_prix.items() if len(set(prix)) > 1}
    if doublons:
        lines += ["", f"── {len(doublons)} référence(s) en doublon (prix différents) ──"]
        for ref, prix_list in list(doublons.items())[:10]:
            prix_fmt = " / ".join(f"{p:.2f}".replace(".", ",") for p in prix_list)
            lines.append(f"  [{ref}] → {prix_fmt}")

    if anomalies:
        lines += ["", f"⚠️  {len(anomalies)} lignes en anomalie → anomalies.csv (à revoir manuellement)."]
    return "\n".join(lines)


def _write_anomalies(anomalies: list[dict], path: str) -> None:
    if not anomalies:
        return
    cols = ["code", "libelle", "prix_brut", "unite", "famille_suggeree", "anomalie_raison"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";",
                           extrasaction="ignore", lineterminator="\r\n")
        w.writeheader()
        w.writerows(anomalies)


def _anomalies_preview(anomalies: list[dict]) -> str:
    if not anomalies:
        return "✅ Aucune anomalie détectée."
    lines = [f"⚠️ {len(anomalies)} anomalie(s) — prix non traçables à la source :"]
    for a in anomalies[:10]:
        lines.append(f"  [{a.get('code','?')}] {str(a.get('libelle',''))[:40]} — {a.get('anomalie_raison','')}")
    if len(anomalies) > 10:
        lines.append(f"  … et {len(anomalies)-10} autres (voir anomalies.csv)")
    return "\n".join(lines)


# ── Gestion des profils ───────────────────────────────────────────────────────

def on_profil_load(profil_nom: str):
    empty = tuple(gr.update() for _ in range(9))
    if not profil_nom or profil_nom == "(aucun)":
        return empty
    try:
        p = load_profile(profil_nom)
        mapping = p.get("collection_famille_map")
        mapping_str = json.dumps(mapping, ensure_ascii=False) if mapping else ""
        regex = p.get("regex_config")
        regex_str = json.dumps(regex, ensure_ascii=False, indent=2) if regex else ""
        return (
            p.get("fournisseur_nom", ""),
            p.get("fournisseur_code", ""),
            p.get("code_famille", "FFR00001"),
            p.get("remise", 45.0),
            p.get("prix_sont_ttc", False),
            p.get("taux_tva_label", "20 %"),
            p.get("unite_defaut", "ml"),
            mapping_str,
            regex_str,
        )
    except Exception:
        return empty


def refresh_profiles():
    choices = ["(aucun)"] + list_profiles()
    return gr.update(choices=choices, value="(aucun)")


# ── Interface Gradio ──────────────────────────────────────────────────────────

with gr.Blocks(title="PDF → CSV EBP", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 📄 Catalogue PDF → CSV EBP Gestion Commerciale")
    gr.Markdown(
        "Convertit un catalogue fournisseur PDF en 3 fichiers CSV prêts à importer dans EBP "
        "(familles → fournisseur → articles). Les prix ne sont jamais inventés : "
        "chaque valeur est tracée au texte source."
    )

    with gr.Row():
        with gr.Column(scale=2):
            gr.Markdown("### 📁 Fichier & pages")
            pdf_input   = gr.File(label="Catalogue PDF", file_types=[".pdf"])
            with gr.Row():
                page_from = gr.Slider(1, 500, value=1, step=1, label="Page début")
                page_to   = gr.Slider(1, 500, value=50, step=1, label="Page fin")
            sample_mode = gr.Checkbox(label="Mode échantillon (30 premières lignes)", value=False)
            with gr.Row():
                btn_debug = gr.Button("🔍 Analyser la structure du PDF", size="sm")
            debug_output = gr.Textbox(label="Structure par page (tables / lignes / prix)", lines=8,
                                      interactive=False, visible=False)

        with gr.Column(scale=2):
            gr.Markdown("### 🏭 Fournisseur")
            with gr.Row():
                profil_dropdown = gr.Dropdown(
                    choices=["(aucun)"] + list_profiles(),
                    value="(aucun)",
                    label="Charger un profil",
                    interactive=True,
                )
                btn_refresh = gr.Button("🔄", size="sm")

            fournisseur_nom  = gr.Textbox(label="Nom du fournisseur *", placeholder="Ex : CASAMANCE")
            fournisseur_code = gr.Textbox(label="Code fournisseur", placeholder="Ex : CAS001")
            code_famille     = gr.Textbox(label="Code famille EBP par défaut", value="FFR00001")
            collection_famille_map_input = gr.Textbox(
                label="Mapping collection → code famille EBP (JSON optionnel)",
                placeholder='{"CHOREGRAPHIE": "TIS-DEC", "PIUMA": "TIS-VEL"}',
                lines=2,
            )
            profil_save_nom  = gr.Textbox(label="Sauvegarder ce profil sous…",
                                          placeholder="laisser vide pour ne pas sauvegarder")

        with gr.Column(scale=1):
            gr.Markdown("### ⚙️ Paramètres")
            remise_input  = gr.Slider(0, 80, value=45, step=1, label="Remise fournisseur (%)")
            prix_ttc_toggle = gr.Checkbox(label="Les prix du catalogue sont TTC", value=False)
            taux_tva   = gr.Radio([l for l, _ in TVA_CHOICES], value="20 %", label="Taux TVA (si prix TTC)")
            unite_def  = gr.Dropdown(UNITE_CHOICES, value="ml", label="Unité par défaut")
            regex_config_input = gr.Textbox(
                label="Extracteur regex (JSON optionnel — prioritaire sur le LLM)",
                placeholder='{"pattern_ligne": "...", "group_nom": 1, ...}',
                lines=3,
            )

    btn_convert = gr.Button("▶️  Lancer la conversion", variant="primary", size="lg")

    with gr.Row():
        zip_output     = gr.File(label="📦 Télécharger les fichiers CSV (ZIP)")
        rapport_output = gr.Textbox(label="📊 Rapport d'audit", lines=20, interactive=False)

    anomalies_output = gr.Textbox(label="⚠️ Anomalies (prix non traçables)", lines=6, interactive=False)
    err_output       = gr.Textbox(label="Erreur", visible=False)

    # ── Événements ──────────────────────────────────────────────────────────
    btn_debug.click(
        fn=run_debug_layout,
        inputs=[pdf_input, page_from, page_to],
        outputs=[debug_output],
    ).then(fn=lambda: gr.update(visible=True), outputs=[debug_output])

    btn_convert.click(
        fn=run_conversion,
        inputs=[
            pdf_input, page_from, page_to,
            fournisseur_nom, fournisseur_code,
            code_famille, remise_input, prix_ttc_toggle,
            taux_tva, unite_def,
            sample_mode, profil_save_nom,
            collection_famille_map_input, regex_config_input,
        ],
        outputs=[zip_output, rapport_output, anomalies_output, err_output],
    )

    profil_dropdown.change(
        fn=on_profil_load,
        inputs=[profil_dropdown],
        outputs=[fournisseur_nom, fournisseur_code, code_famille,
                 remise_input, prix_ttc_toggle, taux_tva, unite_def,
                 collection_famille_map_input, regex_config_input],
    )

    btn_refresh.click(fn=refresh_profiles, outputs=[profil_dropdown])


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
