"""
Calcul du taux de marge à partir de deux colonnes de prix d'un CSV.

Formule retenue (validée) :
    taux de marge = (PV − PA) / PV × 100   (marge commerciale, en %, sur le prix de vente)

PA = prix d'achat HT, PV = prix de vente HT. Le résultat est ajouté en colonne.
Aucune remise n'est fixée au préalable : la marge est déduite des prix présents.
"""
from __future__ import annotations

import pandas as pd

from core.csv_import import parse_price

COL_MARGE = "Taux de marge"

# Détection automatique des colonnes de prix
GUESS_PA = ["achat", "pa", "revient", "cout", "coût", "px achat", "prix achat"]
GUESS_PV = ["vente", "pv", "public", "conseillé", "conseille", "px vente", "prix vente", "tarif"]


def taux_de_marge(pa: float, pv: float) -> float | None:
    """(PV − PA) / PV × 100, arrondi à 2 décimales. None si PV vaut 0."""
    if pv == 0:
        return None
    return round((pv - pa) / pv * 100, 2)


def _fmt_fr(value: float | None) -> str:
    """Décimale française, sans symbole %. None → ''."""
    if value is None:
        return ""
    return f"{value:.2f}".replace(".", ",")


def compute_marges(df: pd.DataFrame, pa_col: str, pv_col: str) -> tuple[pd.DataFrame, dict]:
    """
    Ajoute la colonne « Taux de marge » au DataFrame.

    Retourne (df_out, rapport).
    rapport = {lignes, calculées, pv_nul, marge_negative, moyenne, mini, maxi}
    """
    out = df.copy()
    valeurs: list[str] = []
    marges: list[float] = []
    pv_nul = 0
    negatives = 0

    for _, row in df.iterrows():
        pa = parse_price(row.get(pa_col, ""))
        pv = parse_price(row.get(pv_col, ""))
        tm = taux_de_marge(pa, pv)
        if tm is None:
            pv_nul += 1
            valeurs.append("")
        else:
            valeurs.append(_fmt_fr(tm))
            marges.append(tm)
            if tm < 0:
                negatives += 1

    out[COL_MARGE] = valeurs

    rapport = {
        "lignes":         int(len(df)),
        "calculees":      len(marges),
        "pv_nul":         pv_nul,
        "marge_negative": negatives,
        "moyenne":        round(sum(marges) / len(marges), 2) if marges else None,
        "mini":           round(min(marges), 2) if marges else None,
        "maxi":           round(max(marges), 2) if marges else None,
    }
    return out, rapport
