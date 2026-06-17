"""Tests du nettoyeur CSV (core.cleaner)."""
from core.cleaner import clean_csv, detect_delimiter, detect_encoding


def _opts(**over):
    base = dict(drop_empty=False, drop_duplicates=False, trim_whitespace=False,
                normalize_headers=False, decimal_comma=False, ebp_format=False)
    base.update(over)
    return base


def test_detect_encoding_cp1252():
    raw = "Réf;Prix\né;1".encode("cp1252")
    text, enc = detect_encoding(raw)
    assert "Réf" in text
    assert enc in ("cp1252", "latin-1")


def test_detect_delimiter_semicolon():
    assert detect_delimiter("a;b;c\n1;2;3") == ";"


def test_drop_duplicates():
    raw = b"a,b\n1,2\n1,2\n3,4\n"
    out, rep = clean_csv(raw, _opts(drop_duplicates=True))
    assert rep["lignes_finales"] == 2
    assert out.decode("utf-8-sig").count("1,2") == 1


def test_drop_empty_rows_and_cols():
    raw = b"a,b,vide\n1,2,\n,,\n3,4,\n"
    out, rep = clean_csv(raw, _opts(drop_empty=True))
    assert rep["lignes_finales"] == 2
    assert rep["colonnes_finales"] == 2


def test_trim_whitespace():
    raw = b"a,b\n  x  ,  y  \n"
    out, _ = clean_csv(raw, _opts(trim_whitespace=True))
    assert "x,y" in out.decode("utf-8-sig")


def test_normalize_headers_strips_whitespace():
    raw = b"  nom  ,  prix  \n1,2\n"
    out, _ = clean_csv(raw, _opts(normalize_headers=True))
    header = out.decode("utf-8-sig").splitlines()[0]
    assert header == "nom,prix"


def test_duplicate_headers_are_unique():
    raw = b"nom,nom\n1,2\n"
    out, _ = clean_csv(raw, _opts(normalize_headers=True))
    cols = out.decode("utf-8-sig").splitlines()[0].split(",")
    assert len(set(cols)) == 2  # pas de doublon d'en-tête


def test_decimal_comma_with_thousands():
    raw = "p\n1.234,50\n99.90\n1 234,56\n".encode("utf-8")
    out, _ = clean_csv(raw, _opts(decimal_comma=True))
    body = out.decode("utf-8-sig").splitlines()[1:]
    assert body == ["1234,50", "99,90", "1234,56"]


def test_decimal_comma_keeps_dates():
    raw = b"d\n01.02.2024\n"
    out, _ = clean_csv(raw, _opts(decimal_comma=True))
    assert "01.02.2024" in out.decode("utf-8-sig")


def test_ebp_format_output():
    raw = b"a,b\n1,2\n"
    out, rep = clean_csv(raw, _opts(ebp_format=True))
    assert rep["separateur_sortie"] == ";"
    assert out.startswith(b"\xef\xbb\xbf")  # BOM utf-8-sig
    assert b"\r\n" in out


def test_no_options_reports_clean():
    raw = b"a,b\n1,2\n"
    _out, rep = clean_csv(raw, _opts())
    assert "Aucune modification" in rep["actions"][0]
