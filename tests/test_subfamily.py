"""Tests du module d'analyse des sous-familles + export EBP par famille."""
import csv
import os
import tempfile

from core.subfamily import (
    classify_rules, analyse_sous_familles,
    FAMILLE_DEFAUT, FAMILLES_VALIDES,
)
from core.csv_writer import export_articles_par_famille, COLS_ARTICLES_EBP


# ── Règles déterministes ──────────────────────────────────────────────────────

def test_rules_tapisserie():
    assert classify_rules({"nom_dessin": "Velours côtelé bleu"}) == "TAPISSERIE"

def test_rules_rideau():
    assert classify_rules({"nom_dessin": "Voilage occultant blanc"}) == "RIDEAU"

def test_rules_mousse():
    assert classify_rules({"nom_dessin": "Mousse haute densité 35kg"}) == "MOUSSE"

def test_rules_sellerie():
    assert classify_rules({"nom_dessin": "Simili cuir marine outdoor"}) == "SELLERIE"

def test_rules_collection_utilisee():
    assert classify_rules({"nom_dessin": "Adagio", "collection": "voilages"}) == "RIDEAU"

def test_rules_aucun_match():
    assert classify_rules({"nom_dessin": "Adagio 4172-01"}) is None

def test_rules_word_boundary():
    # "lin" ne doit pas matcher "linge" / "calin"
    assert classify_rules({"nom_dessin": "Calin doudou"}) is None


# ── Analyse complète (sans LLM) ──────────────────────────────────────────────

def test_analyse_sans_llm_defaut():
    articles = [{"nom_dessin": "RefSansIndice"}]
    out, rap = analyse_sous_familles(articles, use_llm=False, client=None)
    assert out[0]["code_sous_famille"] == FAMILLE_DEFAUT
    assert rap["par_defaut"] == 1
    assert rap["total"] == 1

def test_analyse_repartition():
    articles = [
        {"nom_dessin": "Velours bleu"},      # TAPISSERIE
        {"nom_dessin": "Voilage blanc"},     # RIDEAU
        {"nom_dessin": "Mousse 35kg"},       # MOUSSE
    ]
    out, rap = analyse_sous_familles(articles, use_llm=False, client=None)
    assert rap["par_regles"] == 3
    assert rap["repartition"]["TAPISSERIE"] == 1
    assert set(rap["repartition"]) == {"TAPISSERIE", "RIDEAU", "MOUSSE"}

def test_familles_valides_liste_fermee():
    assert "TAPISSERIE" in FAMILLES_VALIDES
    assert "VENTES10" not in FAMILLES_VALIDES  # familles de vente exclues


# ── Export par famille ────────────────────────────────────────────────────────

def test_export_un_fichier_par_famille():
    articles = [
        {"nom_dessin": "Velours bleu", "reference": "V1", "prix_conseille": 50.0,
         "unite": "ml", "code_sous_famille": "TAPISSERIE"},
        {"nom_dessin": "Voilage blanc", "reference": "R1", "prix_conseille": 30.0,
         "unite": "ml", "code_sous_famille": "RIDEAU"},
    ]
    with tempfile.TemporaryDirectory() as d:
        files = export_articles_par_famille(articles, d, fournisseur_code="CAS001")
        assert "articles_TAPISSERIE.csv" in files
        assert "articles_RIDEAU.csv" in files
        for path in files.values():
            assert os.path.exists(path)

def test_export_colonnes_ebp():
    articles = [{"nom_dessin": "Velours", "reference": "V1", "prix_conseille": 50.0,
                 "unite": "ml", "code_sous_famille": "TAPISSERIE"}]
    with tempfile.TemporaryDirectory() as d:
        files = export_articles_par_famille(articles, d, fournisseur_code="CAS001")
        with open(files["articles_TAPISSERIE.csv"], encoding="utf-8-sig") as f:
            header = f.readline().strip().split(";")
        assert header == COLS_ARTICLES_EBP
        assert header[0] == "Code article"
        assert header[-1] == "Code sous-famille article"

def test_export_pv_ttc_calcule():
    # PV HT 100 → PV TTC 120 à 20%
    articles = [{"nom_dessin": "X", "reference": "V1", "prix_conseille": 100.0,
                 "unite": "ml", "code_sous_famille": "TAPISSERIE"}]
    with tempfile.TemporaryDirectory() as d:
        files = export_articles_par_famille(articles, d, fournisseur_code="C",
                                            remise=45.0, taux_tva=20.0)
        with open(files["articles_TAPISSERIE.csv"], encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f, delimiter=";"))
        assert row["PV HT public conseillé"] == "100,00"
        assert row["PV TTC"] == "120,00"
        assert row["Prix d'achat"] == "55,00"  # 100 × (1-0,45)
        assert row["Code sous-famille article"] == "TAPISSERIE"

def test_export_ebp_bom_et_sep():
    articles = [{"nom_dessin": "X", "reference": "V1", "prix_conseille": 50.0,
                 "code_sous_famille": "MOUSSE"}]
    with tempfile.TemporaryDirectory() as d:
        files = export_articles_par_famille(articles, d, fournisseur_code="C")
        with open(files["articles_MOUSSE.csv"], "rb") as f:
            assert f.read(3) == b"\xef\xbb\xbf"
