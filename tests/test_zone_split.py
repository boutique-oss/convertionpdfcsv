"""Tests du découpage en zones (core.zone_split)."""
import csv
import os
import tempfile

from core.zone_split import split_into_zones, write_zones, is_separator


SAMPLE = (
    "Ref;Designation;Prix;Unite\r\n"
    "A1;Article 1;10;ml\r\n"
    "A2;Article 2;20;M\r\n"
    "VELOURS COLLECTION\r\n"          # décalage : 1 cellule → séparateur
    "B1;Article 3;30;U\r\n"
    ";;;\r\n"                          # ligne vide → séparateur
    "C1;Article 4;40;m²\r\n"
)


def _write(tmp_path, content=SAMPLE, name="in.csv"):
    p = os.path.join(tmp_path, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(content)
    return p


# ── Détection séparateur ──────────────────────────────────────────────────────

def test_is_separator_blank():
    assert is_separator(["", "", "", ""], 4) is True

def test_is_separator_titre_une_cellule():
    assert is_separator(["VELOURS"], 4) is True

def test_is_separator_decalage_colonnes():
    assert is_separator(["a", "b"], 4) is True

def test_is_separator_ligne_data_normale():
    assert is_separator(["A1", "Article", "10", "ml"], 4) is False


# ── Découpage ─────────────────────────────────────────────────────────────────

def test_trois_zones():
    with tempfile.TemporaryDirectory() as d:
        zones, rapport = split_into_zones(_write(d))
        assert rapport["nb_zones"] == 3
        assert [len(z["rows"]) for z in zones] == [2, 1, 1]

def test_separateurs_nommes():
    with tempfile.TemporaryDirectory() as d:
        zones, _ = split_into_zones(_write(d))
        assert zones[0]["separateur"] == ""                 # début de fichier
        assert zones[1]["separateur"] == "VELOURS COLLECTION"
        assert zones[2]["separateur"] == ""                 # après ligne vide

def test_write_zones_fichiers_numerotes():
    with tempfile.TemporaryDirectory() as d:
        zones, _ = split_into_zones(_write(d))
        files = write_zones(zones, d)
        assert set(files) == {"zone_01.csv", "zone_02.csv", "zone_03.csv"}

def test_write_zones_header_repete_et_valeurs_preservees():
    with tempfile.TemporaryDirectory() as d:
        zones, _ = split_into_zones(_write(d))
        files = write_zones(zones, d)
        with open(files["zone_03.csv"], encoding="utf-8-sig") as f:
            rows = list(csv.reader(f, delimiter=";"))
        assert rows[0] == ["Ref", "Designation", "Prix", "Unite"]   # header répété
        assert rows[1] == ["C1", "Article 4", "40", "m²"]           # unité préservée

def test_csv_vide():
    with tempfile.TemporaryDirectory() as d:
        zones, rapport = split_into_zones(_write(d, content="", name="empty.csv"))
        assert zones == []
        assert rapport["nb_zones"] == 0
