"""
Écriture des fichiers CSV au format EBP Gestion Commerciale.
Format de référence : CAMENGO import ebp 2026.csv

Colonnes articles :
  Nom de dessin ; Référence ; Collection ; Prix d'achat HT par ml ;
  Prix conseillé HT par ml ; Remise ; Unité ; Fournisseur ;
  Code fournisseur ; Code famille

Règles : sep=";", encoding="utf-8-sig", décimales en virgule française,
         pas de symbole €/%, fin de ligne \\r\\n.
Produit 3 fichiers numérotés dans l'ordre d'import EBP.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path


# ── Colonnes EBP (format de référence CAMENGO) ───────────────────────────────

COLS_ARTICLES = [
    "Nom de dessin",
    "Référence",
    "Collection",
    "Prix d'achat HT par ml",
    "Prix conseillé HT par ml",
    "Remise",
    "Unité",
    "Fournisseur",
    "Code fournisseur",
    "Code famille",
]

COLS_FAMILLES = [
    "Code famille", "Libellé", "Code TVA",
    "Coefficient de marge", "Compte comptable vente", "Compte comptable achat",
]

COLS_FOURNISSEUR = [
    "Code fournisseur", "Nom", "Adresse", "Code postal", "Ville", "Pays",
    "Téléphone", "Email", "Famille fournisseur", "Conditions de règlement",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_decimal(value) -> str:
    """Float ou string → virgule française, sans symbole monétaire ou %."""
    if value is None or value == "":
        return ""
    try:
        f = float(str(value).replace(",", ".").replace("€", "").replace("%", "").strip())
        return f"{f:.2f}".replace(".", ",")
    except (ValueError, TypeError):
        return ""


def _fmt_remise(value) -> str:
    """Remise : entier ou float, sans symbole %."""
    if value is None or value == "":
        return ""
    try:
        f = float(str(value).replace("%", "").replace(",", ".").strip())
        return str(int(f)) if f == int(f) else f"{f:.2f}".replace(".", ",")
    except (ValueError, TypeError):
        return ""


def _truncate(text: str, max_len: int = 80) -> str:
    text = str(text or "").strip()
    return text[:max_len] if len(text) > max_len else text


def _write_csv(rows: list[dict], columns: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter=";",
                           extrasaction="ignore", lineterminator="\r\n")
        w.writeheader()
        for row in rows:
            w.writerow(row)


# ── Calcul prix ──────────────────────────────────────────────────────────────

def prix_achat_from_conseille(prix_conseille_ht: float, remise: float) -> float:
    """Prix achat HT = prix_conseillé_HT × (1 - remise/100). Arrondi 2 décimales."""
    if prix_conseille_ht <= 0:
        return 0.0
    return round(prix_conseille_ht * (1 - remise / 100), 2)


def prix_ht_from_ttc(prix_ttc: float, taux_tva: float = 20.0) -> float:
    """PV HT = prix_TTC / (1 + taux_TVA/100). Utilisé si le catalogue affiche des prix TTC."""
    if prix_ttc <= 0:
        return 0.0
    return round(prix_ttc / (1 + taux_tva / 100), 2)


# ── Export principal ──────────────────────────────────────────────────────────

def export_ebp(
    articles: list[dict],
    output_dir: str,
    fournisseur_nom: str,
    fournisseur_code: str,
    remise: float = 45.0,
    unite_defaut: str = "ml",
    code_famille: str = "FFR00001",
    code_tva: str = "TVA20",
    prix_sont_ttc: bool = False,
    taux_tva: float = 20.0,
    collection_famille_map: dict | None = None,
) -> dict[str, str]:
    """
    Génère les 3 fichiers EBP dans output_dir.
    Retourne un dict {nom_fichier: chemin_absolu}.

    Chaque article doit avoir :
      - nom_dessin       : str
      - reference        : str
      - collection       : str  (→ Collection, sert aussi à déduire la famille)
      - prix_conseille   : float (prix conseillé HT, ou TTC si prix_sont_ttc=True)
      - remise_specifique: float|None (si différente de la remise globale)
      - unite            : str|None

    collection_famille_map : dict optionnel {nom_collection: code_famille_ebp}
      Si fourni, le code famille de chaque article est résolu depuis sa collection.
      Exemple : {"CHOREGRAPHIE": "TIS-DEC", "PIUMA": "TIS-VEL"}
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── 1. Familles ───────────────────────────────────────────────────────────
    # Les collections deviennent des familles (on les regroupe par code famille)
    # Le code famille est unique par fournisseur (ex: FFR00001)
    familles_rows = [{
        "Code famille":             code_famille,
        "Libellé":                  fournisseur_nom,
        "Code TVA":                 code_tva,
        "Coefficient de marge":     "",
        "Compte comptable vente":   "",
        "Compte comptable achat":   "",
    }]
    path_familles = os.path.join(output_dir, "1_familles_articles.csv")
    _write_csv(familles_rows, COLS_FAMILLES, path_familles)

    # ── 2. Fournisseur ────────────────────────────────────────────────────────
    fournisseur_row = [{
        "Code fournisseur":     fournisseur_code,
        "Nom":                  fournisseur_nom,
        "Adresse":              "",
        "Code postal":          "",
        "Ville":                "",
        "Pays":                 "",
        "Téléphone":            "",
        "Email":                "",
        "Famille fournisseur":  "",
        "Conditions de règlement": "",
    }]
    path_fournisseur = os.path.join(output_dir, "2_fournisseur.csv")
    _write_csv(fournisseur_row, COLS_FOURNISSEUR, path_fournisseur)

    # ── 3. Articles ───────────────────────────────────────────────────────────
    articles_rows = []
    for a in articles:
        nom       = _truncate(a.get("nom_dessin") or a.get("libelle") or "", 80)
        ref       = str(a.get("reference") or a.get("code") or "").strip()
        collection = _truncate(a.get("collection") or a.get("famille_suggeree") or "", 80)
        unite_art  = str(a.get("unite") or unite_defaut).strip()
        remise_art = float(a.get("remise_specifique") or remise)

        # Prix conseillé HT
        prix_brut_val = float(a.get("prix_conseille") or a.get("prix_ttc") or 0)
        if prix_sont_ttc and prix_brut_val > 0:
            prix_conseille_ht = prix_ht_from_ttc(prix_brut_val, taux_tva)
        else:
            prix_conseille_ht = prix_brut_val

        # Prix achat HT calculé depuis le conseillé et la remise
        prix_achat_ht = prix_achat_from_conseille(prix_conseille_ht, remise_art)

        # Code famille : résolu depuis le mapping collection si disponible
        famille_art = code_famille
        if collection_famille_map and collection:
            famille_art = collection_famille_map.get(collection, code_famille)

        articles_rows.append({
            "Nom de dessin":            nom,
            "Référence":                ref,
            "Collection":               collection,
            "Prix d'achat HT par ml":   _fmt_decimal(prix_achat_ht) if prix_achat_ht else "",
            "Prix conseillé HT par ml": _fmt_decimal(prix_conseille_ht) if prix_conseille_ht else "",
            "Remise":                   _fmt_remise(remise_art),
            "Unité":                    unite_art,
            "Fournisseur":              fournisseur_nom,
            "Code fournisseur":         fournisseur_code,
            "Code famille":             famille_art,
        })

    path_articles = os.path.join(output_dir, "3_articles.csv")
    _write_csv(articles_rows, COLS_ARTICLES, path_articles)

    return {
        "1_familles_articles.csv": path_familles,
        "2_fournisseur.csv":       path_fournisseur,
        "3_articles.csv":          path_articles,
    }


def _make_famille_code(libelle: str) -> str:
    cleaned = "".join(c for c in libelle.upper() if c.isalpha() or c.isspace())
    words = cleaned.split()
    if not words:
        return "DIV"
    if len(words) == 1:
        return words[0][:6]
    return "".join(w[0] for w in words[:6])


# ── Compatibilité ancienne API (app.py) ───────────────────────────────────────

def to_csv(articles: list[dict], output_path: str, brand: str = "") -> str:
    """Ancienne signature conservée pour compatibilité."""
    out_dir = os.path.dirname(output_path) or "."
    files = export_ebp(
        articles,
        output_dir=out_dir,
        fournisseur_nom=brand,
        fournisseur_code=brand[:8].upper().replace(" ", "") or "FOUR001",
    )
    import shutil
    shutil.copy(files["3_articles.csv"], output_path)
    return output_path
