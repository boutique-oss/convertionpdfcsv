"""
Extraction PDF : pdfplumber (tables en priorité), fallback PyMuPDF texte ligne à ligne.
"""
from __future__ import annotations

import re


PRICE_RE = re.compile(r"\b\d{1,6}[,\.]\d{2}\b")


def debug_layout(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> list[dict]:
    """
    Retourne des stats par page pour diagnostic de structure.
    Chaque dict : {page, tables, lignes_texte, prix_trouves}.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    results = []
    with pdfplumber.open(pdf_path, password=password or None) as pdf:
        start = max(0, page_from - 1)
        end   = min(len(pdf.pages), page_to)
        for i, page in enumerate(pdf.pages[start:end], start=start + 1):
            text   = page.extract_text() or ""
            tables = [t for t in (page.extract_tables() or []) if t and len(t) > 1]
            lines  = [l for l in text.splitlines() if l.strip()]
            prix   = PRICE_RE.findall(text)
            results.append({
                "page":         i,
                "tables":       len(tables),
                "lignes_texte": len(lines),
                "prix_trouves": len(prix),
            })
    return results


def extract_lines_or_tables(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> tuple[list[str], list[str], str]:
    """
    Si pdfplumber détecte des tables valides (>1 ligne), les convertit en lignes pipe-séparées.
    Sinon retourne le texte ligne à ligne.
    Retourne (lignes, prix_source_tokens, mode) où mode = "tables" | "text".
    """
    try:
        import pdfplumber
    except ImportError:
        lines, prix = extract_raw_lines(pdf_path, page_from, page_to, password)
        return lines, prix, "text"

    all_lines: list[str] = []
    full_text_parts: list[str] = []
    found_tables = False

    with pdfplumber.open(pdf_path, password=password or None) as pdf:
        start = max(0, page_from - 1)
        end   = min(len(pdf.pages), page_to)
        for page in pdf.pages[start:end]:
            text   = page.extract_text() or ""
            tables = [t for t in (page.extract_tables() or []) if t and len(t) > 1]
            full_text_parts.append(text)
            if tables:
                found_tables = True
                for table in tables:
                    for row in table:
                        cells = [str(c or "").strip() for c in row]
                        line  = " | ".join(cells)
                        if any(c for c in cells):
                            all_lines.append(line)
            else:
                for l in text.splitlines():
                    if l.strip():
                        all_lines.append(l.strip())

    full_text  = "\n".join(full_text_parts)
    prix_tokens = [p.replace(".", ",") for p in PRICE_RE.findall(full_text)]
    return all_lines, prix_tokens, "tables" if found_tables else "text"


def extract_pages(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> list[str]:
    """
    Retourne une liste de chaînes (une par page) dans la plage 1-based donnée.
    Essaie pdfplumber d'abord, repli sur PyMuPDF si non disponible.
    """
    try:
        return _extract_pdfplumber(pdf_path, page_from, page_to, password)
    except ImportError:
        return _extract_pymupdf(pdf_path, page_from, page_to, password)


def extract_raw_lines(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> tuple[list[str], list[str]]:
    """
    Retourne (lignes_texte, tokens_prix_source).
    tokens_prix_source contient tous les prix trouvés dans le texte brut —
    utilisé ensuite pour valider les prix structurés par le LLM.
    """
    pages = extract_pages(pdf_path, page_from, page_to, password)
    full_text = "\n".join(pages)
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    prix_tokens = PRICE_RE.findall(full_text)
    # Normaliser : remplacer point décimal par virgule pour comparaison uniforme
    prix_tokens_norm = [p.replace(".", ",") for p in prix_tokens]
    return lines, prix_tokens_norm


def extract_tables(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> list[list[list[str | None]]]:
    """
    Extraction structurée par tableaux (pdfplumber uniquement).
    Retourne une liste de tables ; chaque table est une liste de lignes ;
    chaque ligne est une liste de cellules (str ou None).
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    all_tables: list[list[list[str | None]]] = []
    with pdfplumber.open(pdf_path, password=password or None) as pdf:
        start = max(0, page_from - 1)
        end   = min(len(pdf.pages), page_to)
        for page in pdf.pages[start:end]:
            for table in (page.extract_tables() or []):
                if table:
                    all_tables.append(table)
    return all_tables


def has_tables(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> bool:
    return bool(extract_tables(pdf_path, page_from, page_to, password))


# ── Backends ─────────────────────────────────────────────────────────────────

def _extract_pdfplumber(pdf_path, page_from, page_to, password) -> list[str]:
    import pdfplumber
    texts = []
    with pdfplumber.open(pdf_path, password=password or None) as pdf:
        start = max(0, page_from - 1)
        end   = min(len(pdf.pages), page_to)
        for page in pdf.pages[start:end]:
            texts.append(page.extract_text() or "")
    return texts


def _extract_pymupdf(pdf_path, page_from, page_to, password) -> list[str]:
    import fitz
    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        if not doc.authenticate(password):
            raise ValueError("Mot de passe incorrect ou PDF non supporté.")
    start = max(0, page_from - 1)
    end   = min(doc.page_count, page_to)
    return [doc[i].get_text() for i in range(start, end)]
