import fitz  # PyMuPDF


def extract_pages(pdf_path: str, page_from: int, page_to: int) -> list[str]:
    """Return one string per PDF page for the given 1-based page range."""
    doc = fitz.open(pdf_path)
    start = max(0, page_from - 1)
    end = min(doc.page_count, page_to)
    return [doc[i].get_text() for i in range(start, end)]
