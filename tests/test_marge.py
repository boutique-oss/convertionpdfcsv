"""Tests du calcul de taux de marge (core.marge)."""
import pandas as pd
import pytest

from core.marge import taux_de_marge, compute_marges, COL_MARGE


# ── Formule ───────────────────────────────────────────────────────────────────

def test_taux_de_marge_standard():
    # PA 55, PV 100 → (100-55)/100 = 45 %
    assert taux_de_marge(55, 100) == 45.0

def test_taux_de_marge_negatif():
    # PV < PA → marge négative
    assert taux_de_marge(120, 100) == -20.0

def test_taux_de_marge_pv_nul():
    assert taux_de_marge(50, 0) is None

def test_taux_de_marge_arrondi():
    assert taux_de_marge(33, 99) == 66.67


# ── compute_marges ────────────────────────────────────────────────────────────

def _df():
    return pd.DataFrame({
        "Ref":   ["A", "B", "C", "D"],
        "Achat": ["55", "60,00", "120", "10"],
        "Vente": ["100", "120,00", "100", "0"],
    })

def test_colonne_ajoutee():
    out, _ = compute_marges(_df(), "Achat", "Vente")
    assert COL_MARGE in out.columns
    assert list(out[COL_MARGE]) == ["45,00", "50,00", "-20,00", ""]

def test_rapport_stats():
    _, rap = compute_marges(_df(), "Achat", "Vente")
    assert rap["lignes"] == 4
    assert rap["calculees"] == 3       # D a PV=0 → exclu
    assert rap["pv_nul"] == 1
    assert rap["marge_negative"] == 1  # C : PV < PA
    assert rap["moyenne"] == pytest.approx((45 + 50 - 20) / 3, abs=0.01)

def test_colonnes_originales_preservees():
    out, _ = compute_marges(_df(), "Achat", "Vente")
    assert list(out["Ref"]) == ["A", "B", "C", "D"]
    assert "Achat" in out.columns and "Vente" in out.columns
