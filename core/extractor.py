import fitz  # PyMuPDF


def extract_pages(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str = "",
) -> list[str]:
    """Return one string per PDF page for the given 1-based page range."""
    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        if not doc.authenticate(password):
            raise ValueError("Mot de passe incorrect ou PDF non supporté.")
    start = max(0, page_from - 1)
    end = min(doc.page_count, page_to)
    return [doc[i].get_text() for i in range(start, end)]
