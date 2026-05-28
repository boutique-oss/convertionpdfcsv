import json
import re
import anthropic

_SYSTEM = """\
Tu es un extracteur d'articles de catalogue. À partir du texte de pages PDF, \
extrais chaque article et retourne UNIQUEMENT un tableau JSON valide.
Chaque objet doit avoir exactement ces quatre champs :
- "code"     : string  — référence/code article, chaîne vide si absent
- "libelle"  : string  — désignation complète de l'article
- "prix_ttc" : number  — prix TTC en euros (0 si absent ou non trouvé)
- "unite"    : string  — unité de vente ; utilise l'unité par défaut fournie si non précisée
Ne retourne aucun texte, commentaire ou balise avant ou après le tableau JSON.\
"""

_BATCH_SIZE = 5  # pages par appel Claude


def _parse_json_safe(raw: str) -> list[dict]:
    """Try to parse JSON, recovering truncated arrays if possible."""
    raw = raw.strip()
    # Retire les fences markdown
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Tente de récupérer les objets complets si le JSON est tronqué
        objects = re.findall(r'\{[^{}]*\}', raw)
        results = []
        for obj in objects:
            try:
                results.append(json.loads(obj))
            except json.JSONDecodeError:
                continue
        return results


def _call_claude(client: anthropic.Anthropic, batch: list[str], default_unit: str) -> list[dict]:
    combined = "\n\n--- PAGE ---\n\n".join(batch)
    user_msg = f"Unité par défaut : {default_unit}\n\nTexte du catalogue :\n{combined}"
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = message.content[0].text
    return _parse_json_safe(raw)


def extract_articles(
    pages_text: list[str],
    margin_pct: float,
    default_unit: str,
) -> list[dict]:
    """Process pages in batches — skips failed batches, returns all collected articles."""
    client = anthropic.Anthropic(timeout=180.0)
    all_articles: list[dict] = []
    batches = [pages_text[i:i + _BATCH_SIZE] for i in range(0, len(pages_text), _BATCH_SIZE)]

    for i, batch in enumerate(batches):
        try:
            articles = _call_claude(client, batch, default_unit)
            all_articles.extend(articles)
        except Exception:
            # Batch raté → on continue avec les suivants
            continue

    return all_articles
