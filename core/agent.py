import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

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

_BATCH_SIZE = 5   # pages par appel Claude
_BATCH_TIMEOUT = 90  # secondes max par batch


def _parse_json_safe(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Récupère les objets complets si le JSON est tronqué
        results = []
        for obj in re.findall(r'\{[^{}]*\}', raw):
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
    return _parse_json_safe(message.content[0].text)


def extract_articles(
    pages_text: list[str],
    default_unit: str,
    progress_cb=None,
) -> list[dict]:
    """
    Process pages in batches with a per-batch timeout.
    progress_cb(batch_index, total_batches) called after each batch.
    Failed/timed-out batches are skipped.
    """
    client = anthropic.Anthropic(timeout=_BATCH_TIMEOUT + 10)
    all_articles: list[dict] = []
    batches = [pages_text[i:i + _BATCH_SIZE] for i in range(0, len(pages_text), _BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i, batch in enumerate(batches):
            future = pool.submit(_call_claude, client, batch, default_unit)
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
                raise RuntimeError(
                    "Clé API Anthropic invalide ou expirée."
                ) from exc
            except Exception:
                pass  # JSON tronqué ou autre → batch ignoré

            if progress_cb:
                progress_cb(i + 1, len(batches))

    return all_articles
