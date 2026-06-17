"""Tests Phase 5 — validation anti-hallucination des prix."""
import pytest

from core.agent import validate_prices


def test_prix_valide_present_dans_source():
    articles = [{"code": "A001", "libelle": "Tissu", "prix_brut": "42,00", "unite": "ML"}]
    tokens   = ["42,00", "15,50"]
    valides, anomalies = validate_prices(articles, tokens)
    assert len(valides)   == 1
    assert len(anomalies) == 0
    assert valides[0]["prix_conseille"] == pytest.approx(42.0)


def test_prix_absent_de_source_va_en_anomalie():
    articles = [{"code": "A002", "libelle": "Galon", "prix_brut": "99,99", "unite": "M"}]
    tokens   = ["42,00", "15,50"]  # 99,99 n'est pas dans la source
    valides, anomalies = validate_prices(articles, tokens)
    assert len(valides)   == 0
    assert len(anomalies) == 1
    assert "99,99" in anomalies[0]["anomalie_raison"]


def test_prix_vide_accepte():
    articles = [{"code": "A003", "libelle": "Service", "prix_brut": "", "unite": "H"}]
    tokens   = ["42,00"]
    valides, anomalies = validate_prices(articles, tokens)
    assert len(valides)   == 1
    assert valides[0]["prix_conseille"] == 0.0
    assert len(anomalies) == 0


def test_normalisation_point_vers_virgule():
    """Un prix avec point décimal dans la source doit matcher la valeur structurée avec virgule."""
    articles = [{"code": "A004", "libelle": "Tissu", "prix_brut": "42,00", "unite": "ML"}]
    tokens   = ["42.00"]  # source avec point (normalisé en virgule par extractor)
    # extractor normalise déjà : "42.00" → "42,00"
    tokens_norm = [t.replace(".", ",") for t in tokens]
    valides, anomalies = validate_prices(articles, tokens_norm)
    assert len(valides) == 1


def test_mix_valides_et_anomalies():
    articles = [
        {"code": "B001", "libelle": "OK",       "prix_brut": "10,00", "unite": "U"},
        {"code": "B002", "libelle": "Inventé",  "prix_brut": "777,00", "unite": "U"},
        {"code": "B003", "libelle": "Vide",     "prix_brut": "",      "unite": "U"},
    ]
    tokens = ["10,00", "25,00"]
    valides, anomalies = validate_prices(articles, tokens)
    assert len(valides)   == 2  # B001 et B003
    assert len(anomalies) == 1  # B002
    assert anomalies[0]["code"] == "B002"
