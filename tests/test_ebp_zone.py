"""Tests de l'export EBP par zone (colonnes EBP + Taux de marge)."""
import csv
import os
import tempfile

from core.csv_writer import (
    build_ebp_row_marge, export_articles_par_zone, COLS_ARTICLES_EBP_MARGE,
)
from core.csv_import import zone_rows_to_articles


def test_colonnes_ebp_marge():
    assert COLS_ARTICLES_EBP_MARGE[0] == "Code article"
    assert COLS_ARTICLES_EBP_MARGE[-1] == "Taux de marge"
    assert "Code sous-famille article" in COLS_ARTICLES_EBP_MARGE


def test_build_row_marge_calc():
    a = {"reference": "V1", "nom_dessin": "Velours", "unite": "ml",
         "prix_achat": 55.0, "prix_conseille": 100.0, "code_sous_famille": "VELOURS"}
    r = build_ebp_row_marge(a, "CAS001", taux_tva=20.0)
    assert r["PV HT public conseillé"] == "100,00"
    assert r["PV TTC"] == "120,00"
    assert r["Prix d'achat"] == "55,00"
    assert r["Taux de marge"] == "45,00"          # (100-55)/100
    assert r["Code sous-famille article"] == "VELOURS"
    assert r["Code unité"] == "ml"


def test_build_row_marge_pv_nul():
    a = {"reference": "X", "prix_achat": 10.0, "prix_conseille": 0.0}
    r = build_ebp_row_marge(a, "C")
    assert r["Taux de marge"] == ""               # PV=0 → pas de marge


def test_zone_rows_to_articles_tague_zone():
    zones = [
        {"index": 1, "separateur": "", "header": ["Ref", "Lib", "PA", "PV", "U"],
         "rows": [["A1", "Velours", "55", "100", "ml"]]},
        {"index": 2, "separateur": "RIDEAUX", "header": ["Ref", "Lib", "PA", "PV", "U"],
         "rows": [["B1", "Voilage", "20", "40", "M"]]},
    ]
    arts = zone_rows_to_articles(zones, "Ref", "Lib", "PA", "PV", "U")
    assert arts[0]["code_sous_famille"] == "ZONE_01"   # zone sans séparateur
    assert arts[1]["code_sous_famille"] == "RIDEAUX"
    assert arts[0]["prix_achat"] == 55.0 and arts[0]["prix_conseille"] == 100.0
    assert arts[1]["unite"] == "M"


def test_export_un_csv_par_zone():
    arts = [
        {"reference": "A1", "nom_dessin": "Velours", "unite": "ml",
         "prix_achat": 55.0, "prix_conseille": 100.0, "code_sous_famille": "VELOURS"},
        {"reference": "B1", "nom_dessin": "Voilage", "unite": "M",
         "prix_achat": 20.0, "prix_conseille": 40.0, "code_sous_famille": "RIDEAUX"},
    ]
    with tempfile.TemporaryDirectory() as d:
        files = export_articles_par_zone(arts, d, fournisseur_code="CAS001", taux_tva=20.0)
        assert len(files) == 2
        # chaque fichier porte le libellé de la zone
        assert any("VELOURS" in n for n in files)
        assert any("RIDEAUX" in n for n in files)
        path = next(p for n, p in files.items() if "VELOURS" in n)
        with open(path, encoding="utf-8-sig") as f:
            header = f.readline().strip().split(";")
            row = next(csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";"))
        assert header == COLS_ARTICLES_EBP_MARGE
        assert row["Taux de marge"] == "45,00"
        assert row["Code unité"] == "ml"           # unité préservée
