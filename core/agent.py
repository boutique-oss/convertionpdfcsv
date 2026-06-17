"""
Extraction et structuration des articles via Claude.
Anti-hallucination : le LLM structure uniquement, ne calcule jamais de prix.
Chaque prix structuré est validé contre les tokens prix du texte source.
"""
from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

import anthropic

from core.extractor import (
    extract_raw_lines, extract_lines_or_tables,
    extract_tables, has_tables, PRICE_RE,
)


_SYSTEM_TEXT = """\
Tu es un extracteur de données de catalogue textile/décoration.
À partir des lignes brutes fournies, identifie chaque article et retourne UNIQUEMENT un tableau JSON valide.
Chaque objet a exactement ces champs :
- "nom_dessin"   : string — nom du dessin + coloris complet (ex: "Adagio Bleu nuit"), 80 chars max
- "reference"    : string — référence complète incluant suffixe coloris si présent (ex: "4172-01")
- "collection"   : string — nom de la collection ou gamme (vide si absent)
- "prix_brut"    : string — le prix conseillé HT EXACTEMENT tel qu'il apparaît dans le texte \
(ex: "70,92" ou "83,25") — JAMAIS un chiffre inventé, calculé ou interpolé
- "unite"        : string — unité de vente telle qu'écrite (ml, M, U, m², etc.)

RÈGLES ABSOLUES :
- Si un dessin a plusieurs coloris, génère UN objet JSON par coloris avec sa référence propre.
- Si tu ne vois pas de prix dans le texte, mets "prix_brut": "".
- Ne jamais inventer, estimer, recalculer ou interpoler un prix.
- Ne pas confondre prix d'achat et prix conseillé : extrais TOUJOURS le prix public conseillé.
- Ne retourne aucun texte, commentaire ou balise avant ou après le tableau JSON.\
"""

_SYSTEM_VISION = """\
Tu es un extracteur de données de catalogue textile/décoration.
À partir des images de pages fournies, identifie chaque article et retourne UNIQUEMENT un tableau JSON valide.
Chaque objet a exactement ces champs :
- "nom_dessin"   : string — nom du dessin + coloris complet (ex: "Adagio Bleu nuit"), 80 chars max
- "reference"    : string — référence complète incluant suffixe coloris si présent (ex: "4172-01")
- "collection"   : string — nom de la collection ou gamme (vide si absent)
- "prix_brut"    : string — le prix conseillé HT EXACTEMENT tel qu'il est affiché dans l'image \
(ex: "70,92" ou "83,25") — JAMAIS un chiffre inventé, calculé ou interpolé
- "unite"        : string — unité de vente telle qu'écrite (ml, M, U, m², etc.)

RÈGLES ABSOLUES :
- Si un dessin a plusieurs coloris, génère UN objet JSON par coloris avec sa référence propre.
- Si le prix n'est pas lisible ou absent, mets "prix_brut": "".
- Ne jamais inventer, estimer, recalculer ou interpoler un prix.
- Ne pas confondre prix d'achat et prix conseillé : extrais TOUJOURS le prix public conseillé.
- Ne retourne aucun texte, commentaire ou balise avant ou après le tableau JSON.\
"""

_BATCH_SIZE    = 3
_BATCH_TIMEOUT = 120
_DPI           = 150


# ── Parsing LLM ───────────────────────────────────────────────────────────────

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


# ── Validation déterministe des prix ─────────────────────────────────────────

def _normalize_price(prix_brut: str) -> str:
    """Normalise un prix brut pour comparaison : remplace point par virgule."""
    return str(prix_brut or "").strip().replace(".", ",")


def validate_prices(
    articles: list[dict],
    prix_source_tokens: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Sépare les articles en deux listes : valides et anomalies.
    Un prix est valide s'il correspond exactement à un token prix trouvé dans le texte source.
    Un article sans prix (prix_brut="") est accepté directement (prix vide = OK).
    """
    valides: list[dict] = []
    anomalies: list[dict] = []

    source_set = set(prix_source_tokens)

    for a in articles:
        prix_brut = _normalize_price(a.get("prix_brut", ""))
        if not prix_brut:
            # Prix absent dans la source → accepté avec prix_conseille = 0
            a["prix_conseille"] = 0.0
            a["prix_valide"] = True
            valides.append(a)
        elif prix_brut in source_set:
            a["prix_conseille"] = float(prix_brut.replace(",", "."))
            a["prix_valide"] = True
            valides.append(a)
        else:
            a["prix_conseille"] = 0.0
            a["prix_valide"] = False
            a["anomalie_raison"] = f"prix_brut '{prix_brut}' non trouvé dans le texte source"
            anomalies.append(a)

    return valides, anomalies


# ── Multi-coloris ────────────────────────────────────────────────────────────

def expand_coloris(articles: list[dict]) -> list[dict]:
    """
    Si la même référence de base apparaît plusieurs fois avec des noms différents
    (coloris multiples non suffixés), génère des références uniques en -01, -02…
    Ne touche pas aux références déjà suffixées (ex: "4172-01").
    """
    from collections import defaultdict
    groups: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(articles):
        ref = a.get("reference", "")
        if ref:
            groups[ref].append(i)

    result = list(articles)
    for ref, indices in groups.items():
        if len(indices) <= 1:
            continue
        noms = [articles[i].get("nom_dessin", "") for i in indices]
        if len(set(noms)) <= 1:
            continue  # même nom → vraie anomalie doublon, pas multi-coloris
        for j, idx in enumerate(indices, 1):
            a = dict(result[idx])
            a["reference"] = f"{ref}-{j:02d}"
            result[idx] = a
    return result


# ── Appels Claude ─────────────────────────────────────────────────────────────

def _call_claude_text(
    client: anthropic.Anthropic,
    lines: list[str],
    default_unit: str,
) -> list[dict]:
    text_block = "\n".join(lines)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=_SYSTEM_TEXT,
        messages=[{
            "role": "user",
            "content": (
                f"Unité par défaut si non précisée : {default_unit}\n\n"
                f"Lignes brutes du catalogue :\n{text_block}"
            ),
        }],
    )
    return _parse_json_safe(message.content[0].text)


def _normalise_article(a: dict) -> dict:
    """
    Normalise les clés renvoyées par le LLM vers les clés internes attendues.
    Gère les variations de nommage (libelle/nom_dessin, code/reference, etc.).
    """
    # Alias tolerants
    if "libelle" in a and "nom_dessin" not in a:
        a["nom_dessin"] = a.pop("libelle")
    if "code" in a and "reference" not in a:
        a["reference"] = a.pop("code")
    if "famille_suggeree" in a and "collection" not in a:
        a["collection"] = a.pop("famille_suggeree")
    return a


def _page_to_image_b64(page) -> str:
    import fitz
    mat = fitz.Matrix(_DPI / 72, _DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return base64.standard_b64encode(pix.tobytes("png")).decode()


def _call_claude_vision(
    client: anthropic.Anthropic,
    pages,
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
        "text": (
            f"Unité par défaut : {default_unit}\n"
            "Extrais tous les produits de ces pages."
        ),
    })
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=_SYSTEM_VISION,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_json_safe(message.content[0].text)


# ── Point d'entrée principal ──────────────────────────────────────────────────

def extract_articles(
    pdf_path: str,
    page_from: int,
    page_to: int,
    password: str,
    default_unit: str,
    progress_cb: Callable | None = None,
    sample_mode: bool = False,
    sample_lines: int = 30,
    regex_config: dict | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """
    Extrait et structure les articles d'un PDF.

    Retourne (articles_valides, anomalies, rapport).
    rapport = {lues, ecrites, anomalies, taux_couverture, mode}

    Si regex_config est fourni, délègue à extract_with_regex (sans LLM).
    Sinon : extraction texte/tables + LLM Claude.
    """
    if regex_config:
        from core.regex_extractor import extract_with_regex
        return extract_with_regex(
            pdf_path, page_from, page_to, password, regex_config, default_unit,
        )

    # ── Extraction texte source (tables en priorité) ──────────────────────────
    lines, prix_source_tokens, extract_mode = extract_lines_or_tables(
        pdf_path, page_from, page_to, password,
    )

    if sample_mode:
        lines = lines[:sample_lines]

    n_lues = len(lines)

    # ── Structuration LLM ─────────────────────────────────────────────────────
    client = anthropic.Anthropic(timeout=_BATCH_TIMEOUT + 10)
    all_structured: list[dict] = []

    LINE_BATCH = 200
    batches = [lines[i:i + LINE_BATCH] for i in range(0, len(lines), LINE_BATCH)]

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i, batch in enumerate(batches):
            future = pool.submit(_call_claude_text, client, batch, default_unit)
            try:
                structured = [_normalise_article(a) for a in future.result(timeout=_BATCH_TIMEOUT)]
                all_structured.extend(structured)
            except FutureTimeout:
                future.cancel()
            except anthropic.APIConnectionError as exc:
                raise RuntimeError(
                    "Impossible de joindre l'API Anthropic. "
                    "Vérifiez ANTHROPIC_API_KEY."
                ) from exc
            except anthropic.AuthenticationError as exc:
                raise RuntimeError("Clé API Anthropic invalide ou expirée.") from exc
            except Exception:
                pass

            if progress_cb:
                progress_cb(i + 1, len(batches))

    # ── Expansion coloris ─────────────────────────────────────────────────────
    all_structured = expand_coloris(all_structured)

    # ── Validation déterministe ───────────────────────────────────────────────
    valides, anomalies = validate_prices(all_structured, prix_source_tokens)

    rapport = {
        "lignes_lues":         n_lues,
        "articles_ecrits":     len(valides),
        "anomalies":           len(anomalies),
        "taux_couverture_pct": round(len(valides) / max(n_lues, 1) * 100, 1),
        "prix_source_tokens":  len(prix_source_tokens),
        "mode":                extract_mode,
    }

    return valides, anomalies, rapport
