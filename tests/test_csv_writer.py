"""Tests Phase 5 — csv_writer & calcul prix (format de référence CAMENGO)."""
import csv
import os
import tempfile

import pytest

from core.csv_writer import (
    _fmt_decimal,
    _fmt_remise,
    export_ebp,
    prix_achat_from_conseille,
    prix_ht_from_ttc,
)


# ── Calcul prix ───────────────────────────────────────────────────────────────

def test_prix_achat_remise_45():
    # 70,92 × (1 - 45%) = 70,92 × 0,55 = 39,006 → arrondi 39,01
    assert prix_achat_from_conseille(70.92, 45.0) == pytest.approx(39.006, abs=0.01)


def test_prix_achat_remise_0():
    assert prix_achat_from_conseille(100.0, 0.0) == pytest.approx(100.0)


def test_prix_achat_zero_conseille():
    assert prix_achat_from_conseille(0.0, 45.0) == 0.0


def test_prix_ht_from_ttc_20():
    assert prix_ht_from_ttc(120.0, 20.0) == pytest.approx(100.0, abs=0.01)


def test_prix_ht_from_ttc_10():
    assert prix_ht_from_ttc(110.0, 10.0) == pytest.approx(100.0, abs=0.01)


# ── Formatage décimal ─────────────────────────────────────────────────────────

def test_fmt_decimal_virgule():
    assert _fmt_decimal(70.92) == "70,92"


def test_fmt_decimal_no_euro():
    assert "€" not in _fmt_decimal(100.0)


def test_fmt_decimal_no_percent():
    assert "%" not in _fmt_decimal(100.0)


def test_fmt_decimal_empty():
    assert _fmt_decimal("") == ""
    assert _fmt_decimal(None) == ""


def test_fmt_remise_entier():
    assert _fmt_remise(45.0) == "45"
    assert _fmt_remise(45) == "45"


def test_fmt_remise_no_percent():
    assert "%" not in _fmt_remise(45)


# ── Données de test (format CAMENGO) ─────────────────────────────────────────

SAMPLE_ARTICLES = [
    {
        "nom_dessin":   "Adagio",
        "reference":    "3953",
        "collection":   "CHOREGRAPHIE",
        "prix_conseille": 70.92,
        "unite":        "ml",
        "prix_valide":  True,
    },
    {
        "nom_dessin":   "Ballerine",
        "reference":    "3936",
        "collection":   "CHOREGRAPHIE",
        "prix_conseille": 96.08,
        "unite":        "ml",
        "prix_valide":  True,
    },
]


# ── export_ebp — BOM, séparateur, virgule décimale ────────────────────────────

def test_bom_present():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], "rb") as f:
            assert f.read(3) == b"\xef\xbb\xbf", "BOM UTF-8 manquant"


def test_separateur_point_virgule():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            first_line = f.readline()
        assert ";" in first_line


def test_colonnes_format_camengo():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            header = f.readline().strip()
        cols = header.split(";")
        assert cols[0] == "Nom de dessin"
        assert cols[1] == "Référence"
        assert cols[2] == "Collection"
        assert "Prix d'achat HT par ml" in cols
        assert "Prix conseillé HT par ml" in cols
        assert "Remise" in cols


def test_decimal_virgule_dans_articles():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            content = f.read()
        assert "70,92" in content
        assert "." not in content.replace("Nom de dessin", "")  # pas de point décimal


def test_pas_de_euro_dans_articles():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            content = f.read()
        assert "€" not in content


def test_pas_de_percent_dans_articles():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            content = f.read()
        assert "%" not in content


def test_trois_fichiers_produits():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        assert len(files) == 3
        for name in ["1_familles_articles.csv", "2_fournisseur.csv", "3_articles.csv"]:
            assert os.path.exists(files[name])


def test_remise_ecrite_sans_symbole():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                assert row["Remise"] == "45"
                assert "%" not in row["Remise"]


def test_prix_achat_calcule_depuis_conseille():
    # Prix conseillé 70,92 × (1-45%) ≈ 38,51
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        prix_achat = float(row["Prix d'achat HT par ml"].replace(",", "."))
        assert prix_achat == pytest.approx(39.006, abs=0.01)


def test_libelle_tronque_80():
    articles = [{
        "nom_dessin": "X" * 100,
        "reference": "T001",
        "collection": "TEST",
        "prix_conseille": 50.0,
        "unite": "ml",
    }]
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(articles, d, "TEST", "TST001", remise=45.0)
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert len(row["Nom de dessin"]) <= 80


def test_code_famille_dans_articles():
    with tempfile.TemporaryDirectory() as d:
        files = export_ebp(SAMPLE_ARTICLES, d, "CASAMANCE", "CAS001",
                           remise=45.0, code_famille="FFR00001")
        with open(files["3_articles.csv"], encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                assert row["Code famille"] == "FFR00001"
