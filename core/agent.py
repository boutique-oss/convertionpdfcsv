import json
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


def extract_articles(
    pages_text: list[str],
    margin_pct: float,
    default_unit: str,
) -> list[dict]:
    """Call Claude to parse catalogue pages and return a list of article dicts."""
    client = anthropic.Anthropic(timeout=120.0)
    combined = "\n\n--- PAGE ---\n\n".join(pages_text)
    # Tronque si trop long pour éviter les timeouts (≈ 60 pages max)
    if len(combined) > 80_000:
        combined = combined[:80_000]
    user_msg = f"Unité par défaut : {default_unit}\n\nTexte du catalogue :\n{combined}"

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)
