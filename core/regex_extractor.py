"""
Extracteur regex pur — pour catalogues à structure PDF stable d'une année sur l'autre.
Défini dans le profil fournisseur sous la clé "regex_config".
Même interface que extract_articles : retourne (valides, anomalies, rapport).
"""
from __future__ import annotations

import re

from core.extractor import extract_raw_lines
from core.agent import validate_prices


def extract_with_regex(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str,
    regex_config: dict,
    default_unit: str = "ml",
) -> tuple[list[dict], list[dict], dict]:
    """
    Extrait les articles via regex pures, sans appel LLM.

    Clés attendues dans regex_config :
      pattern_ligne   : regex qui matche une ligne article complète (obligatoire)
      group_nom       : numéro de groupe capturant le nom du dessin (défaut 1)
      group_reference : numéro de groupe capturant la référence (défaut 2)
      group_prix      : numéro de groupe capturant le prix brut (défaut 3)
      group_unite     : numéro de groupe capturant l'unité (optionnel)
      collection      : valeur fixe à mettre dans le champ collection (optionnel)
      pattern_ignore  : regex — les lignes qui matchent sont ignorées (optionnel)
    """
    lines, prix_source_tokens = extract_raw_lines(pdf_path, page_from, page_to, password)

    pat_ligne  = re.compile(regex_config.get("pattern_ligne", "(?!x)x"), re.IGNORECASE)
    pat_ignore_str = regex_config.get("pattern_ignore", "")
    pat_ignore = re.compile(pat_ignore_str, re.IGNORECASE) if pat_ignore_str else None

    g_nom   = regex_config.get("group_nom", 1)
    g_ref   = regex_config.get("group_reference", 2)
    g_prix  = regex_config.get("group_prix", 3)
    g_unite = regex_config.get("group_unite")
    collection = regex_config.get("collection", "")

    articles: list[dict] = []
    for line in lines:
        if pat_ignore and pat_ignore.search(line):
            continue
        m = pat_ligne.search(line)
        if not m:
            continue
        try:
            nom   = m.group(g_nom).strip() if g_nom else ""
            ref   = m.group(g_ref).strip() if g_ref else ""
            prix  = m.group(g_prix).strip().replace(".", ",") if g_prix else ""
            unite = m.group(g_unite).strip() if g_unite else default_unit
        except IndexError:
            continue
        articles.append({
            "nom_dessin": nom,
            "reference":  ref,
            "collection": collection,
            "prix_brut":  prix,
            "unite":      unite,
        })

    valides, anomalies = validate_prices(articles, prix_source_tokens)

    rapport = {
        "lignes_lues":         len(lines),
        "articles_ecrits":     len(valides),
        "anomalies":           len(anomalies),
        "taux_couverture_pct": round(len(valides) / max(len(lines), 1) * 100, 1),
        "prix_source_tokens":  len(prix_source_tokens),
        "mode":                "regex",
    }
    return valides, anomalies, rapport
