import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import anthropic

_SYSTEM = """\
Tu es un extracteur de données de catalogue. À partir des images de pages fournies, \
identifie chaque produit, référence, tissu, coloris ou article listé — \
quel que soit le terme utilisé dans le document — \
et retourne UNIQUEMENT un tableau JSON valide.
Chaque objet doit avoir exactement ces quatre champs :
- "code"     : string  — tout code, référence, SKU ou numéro identifiant le produit (vide si absent)
- "libelle"  : string  — désignation, nom, coloris ou description du produit
- "prix_ttc" : number  — tout prix affiché en euros, TTC ou HT (0 si absent)
- "unite"    : string  — unité de vente (utilise l'unité par défaut si non précisée)
Ne retourne aucun texte, commentaire ou balise avant ou après le tableau JSON.\
"""

_BATCH_SIZE = 3    # pages par appel Claude Vision (images = plus lourd)
_BATCH_TIMEOUT = 120
_DPI = 150         # résolution de rendu des pages


def _page_to_image_b64(page) -> str:
    """Render a PDF page to a base64-encoded PNG."""
    import fitz
    mat = fitz.Matrix(_DPI / 72, _DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return base64.standard_b64encode(pix.tobytes("png")).decode()


def _parse_json_safe(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        results = []
        for obj in re.findall(r'\{[^{}]*\}', raw):
            try:
                results.append(json.loads(obj))
            except json.JSONDecodeError:
                continue
        return results


def _call_claude_vision(
    client: anthropic.Anthropic,
    pages,           # list of fitz.Page objects
    default_unit: str,
) -> list[dict]:
    content = []
    for page in pages:
        b64 = _page_to_image_b64(page)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({
        "type": "text",
        "text": f"Unité par défaut : {default_unit}\nExtrait tous les produits/références de ces pages.",
    })

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_json_safe(message.content[0].text)


def extract_articles(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str,
    default_unit: str,
    progress_cb=None,
) -> list[dict]:
    """Render PDF pages as images and extract products via Claude Vision."""
    import fitz
    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        if not doc.authenticate(password):
            raise ValueError("Mot de passe PDF incorrect.")

    start = max(0, page_from - 1)
    end = min(doc.page_count, page_to)
    all_pages = [doc[i] for i in range(start, end)]

    client = anthropic.Anthropic(timeout=_BATCH_TIMEOUT + 10)
    all_articles: list[dict] = []
    batches = [all_pages[i:i + _BATCH_SIZE] for i in range(0, len(all_pages), _BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i, batch in enumerate(batches):
            future = pool.submit(_call_claude_vision, client, batch, default_unit)
            try:
                articles = future.result(timeout=_BATCH_TIMEOUT)
                all_articles.extend(articles)
            except FutureTimeout:
                future.cancel()
            except anthropic.APIConnectionError as exc:
                raise RuntimeError(
                    "Impossible de joindre l'API Anthropic. "
                    "Vérifie ANTHROPIC_API_KEY dans les secrets HF Spaces."
                ) from exc
            except anthropic.AuthenticationError as exc:
                raise RuntimeError("Clé API Anthropic invalide ou expirée.") from exc
            except Exception:
                pass  # batch ignoré

            if progress_cb:
                progress_cb(i + 1, len(batches))

    return all_articles
