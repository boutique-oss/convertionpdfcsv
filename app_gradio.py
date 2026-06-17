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
from core.csv_writer import (
    export_ebp, export_articles_par_famille,
    COLS_ARTICLES_EBP, _build_article_ebp_row,
)
from core.cleaner import clean_csv
from core.extractor import debug_layout
from core.profil import list_profiles, load_profile, save_profile
from core.subfamily import analyse_sous_familles

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


PREVIEW_N = 10  # nombre de références montrées dans l'aperçu éditable


def _build_params(
    fournisseur_nom, fournisseur_code, code_famille, remise, prix_sont_ttc,
    taux_tva_label, unite_defaut, sample_mode, profil_nom_save,
    collection_famille_map_json, regex_config_json, analyse_familles, classif_llm,
) -> dict:
    """Valide et normalise les paramètres. Lève ValueError avec un message FR."""
    fournisseur_nom  = (fournisseur_nom or "").strip()
    fournisseur_code = (fournisseur_code or "").strip().upper()

    if not fournisseur_nom:
        raise ValueError("Le nom du fournisseur est obligatoire.")
    if not fournisseur_code:
        fournisseur_code = fournisseur_nom[:8].upper().replace(" ", "")

    taux_tva = 20.0
    for label, val in TVA_CHOICES:
        if label == taux_tva_label:
            taux_tva = val
            break

    collection_famille_map = None
    if (collection_famille_map_json or "").strip():
        try:
            collection_famille_map = json.loads(collection_famille_map_json)
        except json.JSONDecodeError:
            raise ValueError("JSON du mapping collection invalide.")

    regex_config = None
    if (regex_config_json or "").strip():
        try:
            regex_config = json.loads(regex_config_json)
        except json.JSONDecodeError:
            raise ValueError("JSON de l'extracteur regex invalide.")

    if (profil_nom_save or "").strip():
        profil_data: dict = {
            "fournisseur_nom":  fournisseur_nom,
            "fournisseur_code": fournisseur_code,
            "code_famille":     code_famille,
            "remise":           remise,
            "prix_sont_ttc":    prix_sont_ttc,
            "taux_tva_label":   taux_tva_label,
            "unite_defaut":     unite_defaut,
        }
        if collection_famille_map:
            profil_data["collection_famille_map"] = collection_famille_map
        if regex_config:
            profil_data["regex_config"] = regex_config
        save_profile(profil_nom_save.strip(), profil_data)

    return {
        "fournisseur_nom":        fournisseur_nom,
        "fournisseur_code":       fournisseur_code,
        "code_famille":           code_famille,
        "remise":                 remise,
        "prix_sont_ttc":          prix_sont_ttc,
        "taux_tva":               taux_tva,
        "unite_defaut":           unite_defaut,
        "sample_mode":            sample_mode,
        "collection_famille_map": collection_famille_map,
        "regex_config":           regex_config,
        "analyse_familles":       analyse_familles,
        "classif_llm":            classif_llm,
    }


def _extract_and_analyse(pdf_path, page_from, page_to, params, progress_cb=None):
    """Extraction + analyse des sous-familles. Retourne (valides, anomalies, rapport, rapport_familles)."""
    valides, anomalies, rapport = extract_articles(
        pdf_path=pdf_path,
        page_from=page_from,
        page_to=page_to,
        password="",
        default_unit=params["unite_defaut"],
        progress_cb=progress_cb,
        sample_mode=params["sample_mode"],
        regex_config=params["regex_config"],
    )

    rapport_familles = None
    if params["analyse_familles"] and valides:
        client = None
        if params["classif_llm"]:
            import anthropic
            try:
                client = anthropic.Anthropic()
            except Exception:
                client = None
        valides, rapport_familles = analyse_sous_familles(
            valides, use_llm=params["classif_llm"] and client is not None, client=client,
        )
    return valides, anomalies, rapport, rapport_familles


def _do_export(valides, anomalies, rapport, rapport_familles, params):
    """Écrit tous les CSV + rapport + ZIP. Retourne (zip_path, rapport_txt)."""
    out_dir = tempfile.mkdtemp(prefix="ebp_export_")

    export_ebp(
        articles=valides,
        output_dir=out_dir,
        fournisseur_nom=params["fournisseur_nom"],
        fournisseur_code=params["fournisseur_code"],
        remise=params["remise"],
        unite_defaut=params["unite_defaut"],
        code_famille=params["code_famille"],
        prix_sont_ttc=params["prix_sont_ttc"],
        taux_tva=params["taux_tva"],
        collection_famille_map=params["collection_famille_map"],
    )

    if params["analyse_familles"] and valides:
        export_articles_par_famille(
            articles=valides,
            output_dir=out_dir,
            fournisseur_code=params["fournisseur_code"],
            remise=params["remise"],
            unite_defaut=params["unite_defaut"],
            taux_tva=params["taux_tva"],
            prix_sont_ttc=params["prix_sont_ttc"],
        )

    rapport_txt = _build_rapport(rapport, valides, anomalies,
                                 params["sample_mode"], rapport_familles)
    rapport_path = os.path.join(out_dir, "rapport_conversion.txt")
    Path(rapport_path).write_text(rapport_txt, encoding="utf-8")

    if anomalies:
        _write_anomalies(anomalies, os.path.join(out_dir, "anomalies.csv"))

    zip_path = os.path.join(out_dir, "export_ebp.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in Path(out_dir).glob("*.csv"):
            zf.write(f, f.name)
        zf.write(rapport_path, "rapport_conversion.txt")

    return zip_path, rapport_txt


# ── Aperçu éditable (10 réf.) ────────────────────────────────────────────────

def _articles_to_preview(articles: list[dict], params: dict, n: int) -> list[list[str]]:
    """Premiers n articles → lignes au format colonnes EBP (pour le tableau éditable)."""
    rows = []
    for a in articles[:n]:
        r = _build_article_ebp_row(
            a, params["fournisseur_code"], params["remise"], params["unite_defaut"],
            "TVA20", params["taux_tva"], params["prix_sont_ttc"],
        )
        rows.append([r[c] for c in COLS_ARTICLES_EBP])
    return rows


def _apply_preview_edits(articles: list[dict], edited_rows) -> list[dict]:
    """
    Réinjecte les corrections de l'aperçu (libellé, prix HT, unité, famille)
    dans la liste complète, par appariement sur le « Code article » (référence).
    Les colonnes dérivées (PV TTC, Prix d'achat…) sont recalculées à l'export.
    """
    if edited_rows is None:
        return articles
    rows = edited_rows.values.tolist() if hasattr(edited_rows, "values") else list(edited_rows)

    idx = {c: i for i, c in enumerate(COLS_ARTICLES_EBP)}
    by_ref: dict[str, dict] = {}
    for a in articles:
        ref = str(a.get("reference") or a.get("code") or "").strip()
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


def run_preview(
    pdf_file, page_from, page_to, fournisseur_nom, fournisseur_code, code_famille,
    remise, prix_sont_ttc, taux_tva_label, unite_defaut, sample_mode, profil_nom_save,
    collection_famille_map_json, regex_config_json, analyse_familles, classif_llm,
    progress=gr.Progress(),
):
    if pdf_file is None:
        return None, gr.update(), "⚠️ Aucun fichier PDF fourni.", ""
    try:
        params = _build_params(
            fournisseur_nom, fournisseur_code, code_famille, remise, prix_sont_ttc,
            taux_tva_label, unite_defaut, sample_mode, profil_nom_save,
            collection_famille_map_json, regex_config_json, analyse_familles, classif_llm,
        )
    except ValueError as exc:
        return None, gr.update(), f"⚠️ {exc}", ""

    def _cb(done, total):
        progress(done / max(total, 1), desc=f"Batch {done}/{total}")

    try:
        progress(0, desc="Extraction + analyse pour aperçu…")
        valides, anomalies, rapport, rapport_familles = _extract_and_analyse(
            pdf_file.name, page_from, page_to, params, _cb,
        )
        if not valides and not anomalies:
            return None, gr.update(visible=False), "⚠️ Aucun article détecté.", ""

        preview_rows = _articles_to_preview(valides, params, PREVIEW_N)
        state = {
            "valides": valides, "anomalies": anomalies,
            "rapport": rapport, "rapport_familles": rapport_familles,
            "params": params,
        }
        info = (f"Aperçu de {min(PREVIEW_N, len(valides))} réf. sur {len(valides)}. "
                "Corrige le tableau (famille, libellé, prix HT, unité) puis clique « Exporter ».")
        progress(1.0, desc="Aperçu prêt")
        return (state, gr.update(value=preview_rows, visible=True),
                info, _anomalies_preview(anomalies))
    except Exception as exc:
        tb = traceback.format_exc()
        return None, gr.update(visible=False), f"❌ Erreur : {exc}\n\n{tb[-600:]}", ""


def run_export_from_preview(state, edited_rows, progress=gr.Progress()):
    if not state:
        return None, "⚠️ Lance d'abord un aperçu avant d'exporter.", ""
    try:
        progress(0.2, desc="Application des corrections…")
        valides = _apply_preview_edits(state["valides"], edited_rows)
        params  = state["params"]

        # Répartition familles recalculée après édition
        rapport_familles = state["rapport_familles"]
        if params["analyse_familles"]:
            repart: dict[str, int] = {}
            for a in valides:
                fam = a.get("code_sous_famille", "DIVERS")
                repart[fam] = repart.get(fam, 0) + 1
            if rapport_familles:
                rapport_familles = dict(rapport_familles)
                rapport_familles["repartition"] = dict(sorted(repart.items(), key=lambda kv: -kv[1]))

        progress(0.6, desc="Écriture des CSV…")
        zip_path, rapport_txt = _do_export(
            valides, state["anomalies"], state["rapport"], rapport_familles, params,
        )
        progress(1.0, desc="Terminé !")
        return zip_path, rapport_txt, _anomalies_preview(state["anomalies"])
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-600:]}", ""


def run_conversion(
    pdf_file, page_from, page_to, fournisseur_nom, fournisseur_code, code_famille,
    remise, prix_sont_ttc, taux_tva_label, unite_defaut, sample_mode, profil_nom_save,
    collection_famille_map_json, regex_config_json, analyse_familles, classif_llm,
    progress=gr.Progress(),
):
    """Conversion directe sans aperçu (un seul clic)."""
    if pdf_file is None:
        return None, "⚠️ Aucun fichier PDF fourni.", "", ""
    try:
        params = _build_params(
            fournisseur_nom, fournisseur_code, code_famille, remise, prix_sont_ttc,
            taux_tva_label, unite_defaut, sample_mode, profil_nom_save,
            collection_famille_map_json, regex_config_json, analyse_familles, classif_llm,
        )
    except ValueError as exc:
        return None, f"⚠️ {exc}", "", ""

    def _cb(done, total):
        progress(done / max(total, 1), desc=f"Batch {done}/{total}")

    try:
        progress(0, desc="Extraction en cours…")
        valides, anomalies, rapport, rapport_familles = _extract_and_analyse(
            pdf_file.name, page_from, page_to, params, _cb,
        )
        if not valides and not anomalies:
            return None, "⚠️ Aucun article détecté dans ces pages.", "", ""

        progress(0.9, desc="Écriture des fichiers CSV…")
        zip_path, rapport_txt = _do_export(
            valides, anomalies, rapport, rapport_familles, params,
        )
        progress(1.0, desc="Terminé !")
        return zip_path, rapport_txt, _anomalies_preview(anomalies), ""
    except Exception as exc:
        tb = traceback.format_exc()
        return None, f"❌ Erreur : {exc}\n\n{tb[-800:]}", "", ""


def _build_rapport(rapport: dict, valides, anomalies, sample_mode: bool,
                   rapport_familles: dict | None = None) -> str:
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

    # ── Bilan analyse des sous-familles ───────────────────────────────────────
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


# ── Nettoyeur CSV (onglet) ────────────────────────────────────────────────────

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


# ── Interface Gradio ──────────────────────────────────────────────────────────

with gr.Blocks(title="Atelier — Outils EBP", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🧰 Atelier — Outils CSV / PDF pour EBP Gestion Commerciale")

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

        # ══════════ ONGLET 2 : Extracteur PDF → CSV EBP ══════════
        with gr.Tab("📄 Extracteur PDF → CSV EBP"):
            gr.Markdown(
                "Convertit un catalogue fournisseur PDF en CSV EBP "
                "(familles → fournisseur → articles), avec analyse des sous-familles. "
                "Les prix ne sont jamais inventés : chaque valeur est tracée au texte source."
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

            with gr.Row():
                with gr.Column():
                    gr.Markdown("### 🗂️ Analyse des sous-familles")
                    gr.Markdown(
                        "Classe chaque article dans sa famille EBP "
                        "(TAPISSERIE · RIDEAU · MOUSSE · SELLERIE) et produit "
                        "**1 fichier CSV par famille** au format écran « Articles » EBP."
                    )
                    analyse_familles_toggle = gr.Checkbox(
                        label="Activer l'analyse des sous-familles (export par famille)",
                        value=True,
                    )
                    classif_llm_toggle = gr.Checkbox(
                        label="Affiner les cas ambigus avec Claude (sinon règles seules)",
                        value=True,
                    )

            with gr.Row():
                btn_preview = gr.Button("👁️ Aperçu éditable (10 réf.)", variant="secondary", size="lg")
                btn_convert = gr.Button("▶️ Convertir directement (sans aperçu)", variant="primary", size="lg")

            # ── Aperçu éditable avant export ─────────────────────────────────
            gr.Markdown("### ✏️ Aperçu éditable — corrige avant l'export")
            preview_info = gr.Textbox(label="Aperçu", interactive=False, lines=2)
            preview_table = gr.Dataframe(
                headers=COLS_ARTICLES_EBP,
                datatype=["str"] * len(COLS_ARTICLES_EBP),
                col_count=(len(COLS_ARTICLES_EBP), "fixed"),
                type="pandas",
                interactive=True,
                # « Code article » (clé d'appariement) verrouillé ; colonnes éditables.
                static_columns=[0],
                wrap=True,
                visible=False,
                label="Échantillon — colonnes éditables, sauf « Code article »",
            )
            btn_export = gr.Button("📦 Exporter l'aperçu validé", variant="primary", size="lg")

            preview_state = gr.State()

            with gr.Row():
                zip_output     = gr.File(label="📦 Télécharger les fichiers CSV (ZIP)")
                rapport_output = gr.Textbox(label="📊 Rapport d'audit", lines=20, interactive=False)

            anomalies_output = gr.Textbox(label="⚠️ Anomalies (prix non traçables)", lines=6, interactive=False)
            err_output       = gr.Textbox(label="Erreur", visible=False)

    _convert_inputs = [
        pdf_input, page_from, page_to,
        fournisseur_nom, fournisseur_code,
        code_famille, remise_input, prix_ttc_toggle,
        taux_tva, unite_def,
        sample_mode, profil_save_nom,
        collection_famille_map_input, regex_config_input,
        analyse_familles_toggle, classif_llm_toggle,
    ]

    # ── Événements ──────────────────────────────────────────────────────────
    btn_clean.click(
        fn=run_clean,
        inputs=[clean_input, opt_drop_empty, opt_drop_dup, opt_trim, opt_headers,
                opt_remove_sym, opt_digits, opt_decimal, opt_ebp],
        outputs=[clean_file_out, clean_report_out],
    )

    btn_debug.click(
        fn=run_debug_layout,
        inputs=[pdf_input, page_from, page_to],
        outputs=[debug_output],
    ).then(fn=lambda: gr.update(visible=True), outputs=[debug_output])

    btn_preview.click(
        fn=run_preview,
        inputs=_convert_inputs,
        outputs=[preview_state, preview_table, preview_info, anomalies_output],
    )

    btn_export.click(
        fn=run_export_from_preview,
        inputs=[preview_state, preview_table],
        outputs=[zip_output, rapport_output, anomalies_output],
    )

    btn_convert.click(
        fn=run_conversion,
        inputs=_convert_inputs,
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
