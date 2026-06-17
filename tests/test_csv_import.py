"""Tests de l'ingestion CSV + mapping (core.csv_import)."""
import pandas as pd

from core.csv_import import parse_price, guess_col, csv_to_articles, GUESS, NONE_COL


# ── parse_price ───────────────────────────────────────────────────────────────

def test_parse_price_virgule():
    assert parse_price("70,92") == 70.92

def test_parse_price_euro_et_espace():
    assert parse_price("1 234,50 €") == 1234.50

def test_parse_price_entier():
    assert parse_price("45") == 45.0

def test_parse_price_vide():
    assert parse_price("") == 0.0
    assert parse_price(None) == 0.0

def test_parse_price_non_numerique():
    assert parse_price("sur devis") == 0.0


# ── guess_col ─────────────────────────────────────────────────────────────────

def test_guess_col_reference():
    cols = ["Code Article", "Désignation", "Tarif"]
    assert guess_col(cols, GUESS["reference"]) == "Code Article"

def test_guess_col_libelle():
    cols = ["Ref", "Désignation", "PU"]
    assert guess_col(cols, GUESS["libelle"]) == "Désignation"

def test_guess_col_aucun():
    assert guess_col(["X", "Y"], GUESS["prix"]) is None


# ── csv_to_articles : unités variables préservées ────────────────────────────

def _df():
    return pd.DataFrame({
        "Ref":        ["V1", "R1", "M1", "S1"],
        "Designation":["Velours bleu", "Voilage", "Mousse 35kg", "Simili cuir"],
        "Tarif":      ["70,92", "30,00", "12,50 €", "45"],
        "Cond":       ["ml", "M", "U", "m²"],
    })

def test_csv_to_articles_basic():
    arts = csv_to_articles(_df(), "Ref", "Designation", "Tarif", "Cond")
    assert len(arts) == 4
    assert arts[0]["reference"] == "V1"
    assert arts[0]["nom_dessin"] == "Velours bleu"
    assert arts[0]["prix_conseille"] == 70.92

def test_csv_to_articles_unites_telles_quelles():
    arts = csv_to_articles(_df(), "Ref", "Designation", "Tarif", "Cond")
    unites = [a["unite"] for a in arts]
    # chaque ligne garde son unité propre, aucune uniformisation
    assert unites == ["ml", "M", "U", "m²"]

def test_csv_to_articles_sans_colonne_unite():
    arts = csv_to_articles(_df(), "Ref", "Designation", "Tarif", NONE_COL)
    assert all(a["unite"] == "" for a in arts)  # vide, jamais une valeur imposée

def test_csv_to_articles_sans_prix():
    arts = csv_to_articles(_df(), "Ref", "Designation", NONE_COL, "Cond")
    assert all(a["prix_conseille"] == 0.0 for a in arts)

def test_csv_to_articles_ignore_lignes_vides():
    df = pd.DataFrame({"Ref": ["A", ""], "Designation": ["x", ""], "Tarif": ["1", ""], "Cond": ["ml", ""]})
    arts = csv_to_articles(df, "Ref", "Designation", "Tarif", "Cond")
    assert len(arts) == 1
