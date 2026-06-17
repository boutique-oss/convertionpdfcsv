"""
Tests pour les nouvelles fonctionnalités :
  1. expand_coloris (multi-coloris)
  2. collection_famille_map dans export_ebp
  3. extract_with_regex (mode regex)
"""
import csv
import os
import tempfile

import pytest

from core.agent import expand_coloris
from core.csv_writer import export_ebp
from core.regex_extractor import extract_with_regex


# ── 1. expand_coloris ────────────────────────────────────────────────────────

def test_expand_coloris_same_ref_different_names():
    articles = [
        {"reference": "4172", "nom_dessin": "Adagio Bleu nuit", "prix_conseille": 70.0},
        {"reference": "4172", "nom_dessin": "Adagio Ivoire",    "prix_conseille": 70.0},
        {"reference": "4172", "nom_dessin": "Adagio Rouge",     "prix_conseille": 72.0},
    ]
    result = expand_coloris(articles)
    refs = [a["reference"] for a in result]
    assert refs == ["4172-01", "4172-02", "4172-03"]


def test_expand_coloris_no_change_when_names_identical():
    articles = [
        {"reference": "9999", "nom_dessin": "Tissu X", "prix_conseille": 50.0},
        {"reference": "9999", "nom_dessin": "Tissu X", "prix_conseille": 50.0},
    ]
    result = expand_coloris(articles)
    # Même nom → considéré doublon, référence non modifiée
    refs = [a["reference"] for a in result]
    assert refs == ["9999", "9999"]


def test_expand_coloris_already_suffixed_not_touched():
    articles = [
        {"reference": "4172-01", "nom_dessin": "Adagio Bleu", "prix_conseille": 70.0},
        {"reference": "4172-02", "nom_dessin": "Adagio Rouge", "prix_conseille": 70.0},
    ]
    result = expand_coloris(articles)
    refs = [a["reference"] for a in result]
    assert refs == ["4172-01", "4172-02"]


def test_expand_coloris_unique_ref_unchanged():
    articles = [
        {"reference": "A001", "nom_dessin": "Uni",     "prix_conseille": 30.0},
        {"reference": "A002", "nom_dessin": "Rayures", "prix_conseille": 35.0},
    ]
    result = expand_coloris(articles)
    assert [a["reference"] for a in result] == ["A001", "A002"]


# ── 2. collection_famille_map ─────────────────────────────────────────────────

SAMPLE_ARTICLES = [
    {
        "nom_dessin": "Adagio", "reference": "3953",
        "collection": "CHOREGRAPHIE", "prix_conseille": 70.92, "unite": "ml",
    },
    {
        "nom_dessin": "Ballerine", "reference": "3936",
        "collection": "PIUMA", "prix_conseille": 96.08, "unite": "ml",
    },
    {
        "nom_dessin": "Inconnu", "reference": "0000",
        "collection": "AUTRE", "prix_conseille": 50.0, "unite": "ml",
    },
]

MAPPING = {"CHOREGRAPHIE": "TIS-DEC", "PIUMA": "TIS-VEL"}


def test_collection_famille_map_applied():
    with tempfile.TemporaryDirectory() as d:
        export_ebp(SAMPLE_ARTICLES, d, "TEST", "TST001",
                   remise=45.0, collection_famille_map=MAPPING)
        with open(os.path.join(d, "3_articles.csv"), encoding="utf-8-sig") as f:
            reader = list(csv.DictReader(f, delimiter=";"))
    assert reader[0]["Code famille"] == "TIS-DEC"
    assert reader[1]["Code famille"] == "TIS-VEL"


def test_collection_famille_map_fallback_to_default():
    with tempfile.TemporaryDirectory() as d:
        export_ebp(SAMPLE_ARTICLES, d, "TEST", "TST001",
                   remise=45.0, code_famille="DEF001",
                   collection_famille_map=MAPPING)
        with open(os.path.join(d, "3_articles.csv"), encoding="utf-8-sig") as f:
            reader = list(csv.DictReader(f, delimiter=";"))
    # "AUTRE" n'est pas dans le mapping → doit utiliser code_famille par défaut
    assert reader[2]["Code famille"] == "DEF001"


def test_no_collection_famille_map_uses_default():
    with tempfile.TemporaryDirectory() as d:
        export_ebp(SAMPLE_ARTICLES, d, "TEST", "TST001",
                   remise=45.0, code_famille="GEN001")
        with open(os.path.join(d, "3_articles.csv"), encoding="utf-8-sig") as f:
            reader = list(csv.DictReader(f, delimiter=";"))
    for row in reader:
        assert row["Code famille"] == "GEN001"


# ── 3. Extracteur regex ───────────────────────────────────────────────────────

def _make_temp_pdf_with_text(text: str) -> str:
    """Crée un PDF minimal via reportlab (si disponible) ou renvoie None."""
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        c = rl_canvas.Canvas(f.name)
        y = 750
        for line in text.splitlines():
            c.drawString(50, y, line)
            y -= 15
        c.save()
        return f.name
    except ImportError:
        return None


def test_regex_extractor_basic(tmp_path):
    """Test l'extracteur regex sur du texte synthétique."""
    # Simuler des lignes comme pdfplumber les extrairait
    from unittest.mock import patch

    fake_lines = [
        "HOULÈS 001    Tissu Velours    42,00 ml",
        "HOULÈS 002    Tissu Satin      56,50 ml",
        "====== Entête à ignorer ======",
        "HOULÈS 003    Tissu Lin        33,00 ml",
    ]
    fake_prix = ["42,00", "56,50", "33,00"]

    regex_config = {
        "pattern_ligne":    r"HOULÈS\s+(\d+)\s+(.+?)\s+(\d+,\d{2})\s+ml",
        "group_nom":        2,
        "group_reference":  1,
        "group_prix":       3,
        "pattern_ignore":   r"={3,}",
        "collection":       "HOULES",
    }

    with patch("core.regex_extractor.extract_raw_lines", return_value=(fake_lines, fake_prix)):
        valides, anomalies, rapport = extract_with_regex(
            "fake.pdf", 1, 1, "", regex_config, "ml",
        )

    assert len(valides) == 3
    assert len(anomalies) == 0
    assert rapport["mode"] == "regex"
    refs = [a["reference"] for a in valides]
    assert "001" in refs and "002" in refs and "003" in refs


def test_regex_extractor_bad_price_goes_to_anomaly():
    from unittest.mock import patch

    fake_lines = ["HOULÈS 004    Tissu Coton    99,99 ml"]
    fake_prix  = ["42,00"]  # 99,99 absent de la source

    regex_config = {
        "pattern_ligne":   r"HOULÈS\s+(\d+)\s+(.+?)\s+(\d+,\d{2})\s+ml",
        "group_nom":       2,
        "group_reference": 1,
        "group_prix":      3,
    }

    with patch("core.regex_extractor.extract_raw_lines", return_value=(fake_lines, fake_prix)):
        valides, anomalies, rapport = extract_with_regex(
            "fake.pdf", 1, 1, "", regex_config, "ml",
        )

    assert len(valides) == 0
    assert len(anomalies) == 1


def test_regex_extractor_pattern_ignore():
    from unittest.mock import patch

    fake_lines = [
        "HOULÈS 010  Tissu A  10,00 ml",
        "Page titre — à ignorer",
        "HOULÈS 011  Tissu B  20,00 ml",
    ]
    fake_prix = ["10,00", "20,00"]

    regex_config = {
        "pattern_ligne":   r"HOULÈS\s+(\d+)\s+(.+?)\s+(\d+,\d{2})\s+ml",
        "group_nom":       2,
        "group_reference": 1,
        "group_prix":      3,
        "pattern_ignore":  r"titre",
    }

    with patch("core.regex_extractor.extract_raw_lines", return_value=(fake_lines, fake_prix)):
        valides, anomalies, _ = extract_with_regex(
            "fake.pdf", 1, 1, "", regex_config, "ml",
        )

    assert len(valides) == 2
