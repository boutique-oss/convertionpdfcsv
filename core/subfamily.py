"""
Module « Analyse des sous-familles » — s'intercale entre l'extraction et le
nettoyage/export.

Objectif : pour chaque article, déchiffrer sa FAMILLE PRIMAIRE article EBP
(MOUSSE / RIDEAU / SELLERIE / TAPISSERIE…) et l'écrire dans une colonne unique
et constante : `code_sous_famille`.

Méthode HYBRIDE (validée) :
  1. Règles déterministes par mots-clés (rapide, traçable, sans appel API).
  2. Les articles non tranchés par les règles passent au LLM Claude, contraint
     à choisir UNIQUEMENT dans la liste des familles EBP connues.

Aucune valeur n'est inventée : le LLM ne fait que CLASSER dans une liste fermée.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Callable

import anthropic


# ── Familles article EBP (capture utilisateur) ───────────────────────────────
# Seules les familles « matière » sont des cibles de classification.
# VENTES10 / VENTES20 sont des familles de vente (TVA), pas des matières : exclues.

FAMILLE_DEFAUT = "TAPISSERIE"  # cas le plus fréquent pour un tissu d'ameublement

# Ordre = priorité : la première famille dont un mot-clé matche l'emporte.
FAMILLE_KEYWORDS: dict[str, list[str]] = {
    "SELLERIE": [
        "cuir", "simili", "similicuir", "skai", "skaï", "vinyle", "sellerie",
        "nautique", "marine", "outdoor", "exterieur", "bateau", "auto",
    ],
    "MOUSSE": [
        "mousse", "ouate", "ouatine", "garnissage", "rembourrage", "crin",
        "plume", "polyester", "molleton", "feutre",
    ],
    "RIDEAU": [
        "rideau", "voilage", "voile", "occultant", "tamisant", "store",
        "embrasse", "tringle", "double rideau", "etamine", "étamine",
    ],
    "TAPISSERIE": [
        "velours", "tissu", "tisse", "tissé", "jacquard", "chenille", "lin",
        "coton", "laine", "ameublement", "siege", "siège", "fauteuil",
        "tapisserie", "damas", "satin", "gabardine", "boucle", "bouclette",
    ],
}

# Liste fermée transmise au LLM
FAMILLES_VALIDES = list(FAMILLE_KEYWORDS.keys())


# ── Normalisation ─────────────────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(text: str) -> str:
    """Minuscule + sans accents pour comparaison mots-clés."""
    return _strip_accents(str(text or "").lower())


# ── 1. Règles déterministes ───────────────────────────────────────────────────

def classify_rules(article: dict) -> str | None:
    """
    Retourne le code famille EBP si un mot-clé matche, sinon None.
    Cherche dans le libellé/nom de dessin + collection.
    """
    haystack = _norm(
        f"{article.get('nom_dessin', '')} "
        f"{article.get('libelle', '')} "
        f"{article.get('collection', '')}"
    )
    for famille, keywords in FAMILLE_KEYWORDS.items():
        for kw in keywords:
            kw_n = _strip_accents(kw.lower())
            # \b pour éviter les faux positifs (ex: "lin" dans "linge"),
            # s? optionnel pour tolérer le pluriel (voilage → voilages).
            if re.search(rf"\b{re.escape(kw_n)}s?\b", haystack):
                return famille
    return None


# ── 2. Fallback LLM (liste fermée) ────────────────────────────────────────────

_SYSTEM_CLASSIFY = """\
Tu es un classificateur d'articles de tapisserie/décoration pour le logiciel EBP.
On te donne une liste d'articles (libellé + collection).
Pour CHAQUE article, tu choisis sa famille parmi cette liste FERMÉE et UNIQUEMENT \
celle-ci :
{familles}

Définitions :
- TAPISSERIE : tissu d'ameublement, velours, jacquard, lin, coton pour sièges/fauteuils (défaut).
- RIDEAU    : voilage, occultant, tamisant, tissu pour rideaux/stores.
- MOUSSE    : mousse, ouate, garnissage, rembourrage, crin.
- SELLERIE  : cuir, simili, vinyle, tissus outdoor/nautique.

Réponds UNIQUEMENT par un tableau JSON de chaînes, une par article, dans l'ordre.
Exemple : ["TAPISSERIE", "RIDEAU", "TAPISSERIE"]
Aucune autre valeur que celles de la liste. Aucun texte avant ou après le JSON.\
"""


def _parse_json_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return re.findall(r'"([^"]+)"', raw)


def classify_llm(
    articles: list[dict],
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-6",
) -> list[str]:
    """
    Classe une liste d'articles via Claude. Retourne une famille par article,
    toujours dans FAMILLES_VALIDES (toute valeur hors liste → FAMILLE_DEFAUT).
    """
    if not articles:
        return []

    lignes = []
    for i, a in enumerate(articles):
        nom = a.get("nom_dessin") or a.get("libelle") or ""
        col = a.get("collection") or ""
        lignes.append(f"{i+1}. {nom} | collection: {col}")

    message = client.messages.create(
        model=model,
        max_tokens=4000,
        system=_SYSTEM_CLASSIFY.format(familles=", ".join(FAMILLES_VALIDES)),
        messages=[{"role": "user", "content": "\n".join(lignes)}],
    )
    result = _parse_json_list(message.content[0].text)

    # Aligne la longueur et borne aux familles valides
    out: list[str] = []
    for i in range(len(articles)):
        fam = result[i] if i < len(result) else FAMILLE_DEFAUT
        fam = fam.strip().upper()
        out.append(fam if fam in FAMILLES_VALIDES else FAMILLE_DEFAUT)
    return out


# ── Point d'entrée ─────────────────────────────────────────────────────────────

def analyse_sous_familles(
    articles: list[dict],
    use_llm: bool = True,
    client: anthropic.Anthropic | None = None,
    progress_cb: Callable | None = None,
) -> tuple[list[dict], dict]:
    """
    Annote chaque article avec `code_sous_famille` (famille primaire EBP).

    1. Règles d'abord (mots-clés).
    2. Les restants → LLM si use_llm=True et client fourni, sinon FAMILLE_DEFAUT.

    Retourne (articles_annotés, rapport).
    rapport = {total, par_regles, par_llm, par_defaut, repartition: {fam: n}}
    """
    par_regles = 0
    indices_llm: list[int] = []

    for i, a in enumerate(articles):
        fam = classify_rules(a)
        if fam:
            a["code_sous_famille"] = fam
            a["famille_source"] = "regle"
            par_regles += 1
        else:
            indices_llm.append(i)

    par_llm = 0
    par_defaut = 0

    if indices_llm:
        if use_llm and client is not None:
            restants = [articles[i] for i in indices_llm]
            try:
                familles = classify_llm(restants, client)
            except Exception:
                familles = [FAMILLE_DEFAUT] * len(restants)
            for idx, fam in zip(indices_llm, familles):
                articles[idx]["code_sous_famille"] = fam
                articles[idx]["famille_source"] = "llm"
                par_llm += 1
            if progress_cb:
                progress_cb(1, 1)
        else:
            for i in indices_llm:
                articles[i]["code_sous_famille"] = FAMILLE_DEFAUT
                articles[i]["famille_source"] = "defaut"
                par_defaut += 1

    repartition: dict[str, int] = {}
    for a in articles:
        fam = a.get("code_sous_famille", FAMILLE_DEFAUT)
        repartition[fam] = repartition.get(fam, 0) + 1

    rapport = {
        "total":       len(articles),
        "par_regles":  par_regles,
        "par_llm":     par_llm,
        "par_defaut":  par_defaut,
        "repartition": dict(sorted(repartition.items(), key=lambda kv: -kv[1])),
    }
    return articles, rapport
