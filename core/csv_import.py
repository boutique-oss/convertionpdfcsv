"""
Lecture d'un CSV fournisseur + mapping de ses colonnes vers les champs article.

L'entrée est toujours un CSV (structure variable selon le fournisseur) ; on lit
les colonnes réelles, on devine un mapping par défaut, et on construit des dicts
article exploitables par core.subfamily / core.csv_writer.

Règle d'or : l'unité de chaque ligne est prise TELLE QUELLE, jamais imposée ni
uniformisée pour l'ensemble du fichier.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

from core.cleaner import detect_delimiter, detect_encoding

NONE_COL = "(aucune)"


def read_csv_df(path: str) -> pd.DataFrame:
    """Lit le CSV en str ; encodage + séparateur auto-détectés."""
    raw = Path(path).read_bytes()
    text, _enc = detect_encoding(raw)
    delim = detect_delimiter(text)
    return pd.read_csv(
        io.StringIO(text), sep=delim, dtype=str, keep_default_na=False,
        engine="python", on_bad_lines="skip",
    )


def guess_col(cols: list[str], keywords: list[str]) -> str | None:
    """Premier en-tête contenant un des mots-clés (insensible à la casse)."""
    for c in cols:
        cl = str(c).lower()
        if any(k in cl for k in keywords):
            return c
    return None


# Mots-clés de détection automatique par champ
GUESS = {
    "reference": ["référence", "reference", "réf", "ref", "code article", "code"],
    "libelle":   ["libellé", "libelle", "désignation", "designation", "nom", "dessin", "article", "produit"],
    "prix":      ["prix", "pv", "tarif", "montant", "conseillé"],
    "unite":     ["unité", "unite", "unit", "cond", "vente", "ml"],
}


def parse_price(val) -> float:
    """'70,92 €' / '1 234,50' → float. Vide ou non numérique → 0.0."""
    s = str(val or "").strip()
    if not s:
        return 0.0
    s = s.replace(" ", "").replace(" ", "").replace("€", "")
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else 0.0


def zone_rows_to_articles(zones: list[dict], ref_col: str, lib_col: str,
                          pa_col: str | None, pv_col: str | None,
                          unite_col: str | None) -> list[dict]:
    """
    Convertit les lignes des zones (issues de core.zone_split) en dicts article,
    chaque article tagué avec sa zone (code_sous_famille = libellé du séparateur).

    Le mapping se fait par nom de colonne, résolu en index via l'en-tête commun.
    """
    if not zones:
        return []
    header = [str(c) for c in zones[0]["header"]]

    def idx(name):
        return header.index(name) if name and name != NONE_COL and name in header else None

    i_ref, i_lib = idx(ref_col), idx(lib_col)
    i_pa, i_pv, i_un = idx(pa_col), idx(pv_col), idx(unite_col)

    def cell(row, i):
        return str(row[i]).strip() if (i is not None and i < len(row)) else ""

    articles: list[dict] = []
    for z in zones:
        label = z.get("separateur") or f"ZONE_{z['index']:02d}"
        for row in z["rows"]:
            a = {
                "reference":         cell(row, i_ref),
                "nom_dessin":        cell(row, i_lib),
                "prix_achat":        parse_price(cell(row, i_pa)) if i_pa is not None else 0.0,
                "prix_conseille":    parse_price(cell(row, i_pv)) if i_pv is not None else 0.0,
                "unite":             cell(row, i_un),
                "code_sous_famille": label,
            }
            if a["reference"] or a["nom_dessin"]:
                articles.append(a)
    return articles


def csv_to_articles(df: pd.DataFrame, ref_col: str, lib_col: str,
                    prix_col: str | None, unite_col: str | None) -> list[dict]:
    """
    Convertit les lignes du DataFrame en dicts article.
    `prix_col`/`unite_col` peuvent valoir None ou NONE_COL (champ absent).
    """
    articles: list[dict] = []
    for _, row in df.iterrows():
        a = {
            "reference":  str(row.get(ref_col, "")).strip(),
            "nom_dessin": str(row.get(lib_col, "")).strip(),
        }
        a["prix_conseille"] = (
            parse_price(row.get(prix_col, ""))
            if prix_col and prix_col != NONE_COL else 0.0
        )
        # Unité telle quelle, ligne par ligne ; jamais imposée.
        a["unite"] = (
            str(row.get(unite_col, "")).strip()
            if unite_col and unite_col != NONE_COL else ""
        )
        if a["reference"] or a["nom_dessin"]:
            articles.append(a)
    return articles
