"""
Découpage d'un CSV en ZONES.

Principe : on lit TOUTES les lignes du CSV (sans en ignorer aucune, contrairement
à pandas qui saute les lignes mal formées). Chaque fois qu'une ligne marque une
rupture — une SÉPARATION (ligne vide) ou un DÉCALAGE de données (ligne de titre de
section dont la structure de colonnes diffère des lignes de données) — on coupe :
tout ce qui suit forme une nouvelle zone, écrite dans son propre CSV.

Les fichiers sortent numérotés (zone_01.csv, zone_02.csv…) ; l'utilisateur leur
donne lui-même un intitulé. Le rapport indique le séparateur de chaque zone.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from core.cleaner import detect_delimiter, detect_encoding


def _filled(row: list[str]) -> int:
    return sum(1 for c in row if str(c).strip())


def _read_rows(path: str) -> tuple[list[list[str]], str]:
    """Lit toutes les lignes brutes du CSV (aucune ligne ignorée)."""
    raw = Path(path).read_bytes()
    text, _enc = detect_encoding(raw)
    delim = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim if delim != "\x00" else ";")
    return [list(r) for r in reader], (delim if delim != "\x00" else ";")


def _pick_header(rows: list[list[str]]) -> int:
    """Index de la ligne d'en-tête = première ligne avec au moins 2 cellules remplies."""
    for i, r in enumerate(rows):
        if _filled(r) >= 2:
            return i
    return 0


def is_separator(row: list[str], header_len: int) -> bool:
    """
    Vrai si la ligne marque une rupture de zone (NOUVELLE SECTION) :
      - ligne entièrement vide (séparation), ou
      - une seule cellule remplie alors que l'en-tête a ≥3 colonnes (titre de section).

    Un simple écart de nombre de colonnes n'est PAS une rupture : c'est une ligne
    de données décalée, qui sera réalignée dans sa zone (cf. realign_rows).
    """
    f = _filled(row)
    if f == 0:
        return True
    if f == 1 and header_len >= 3:
        return True
    return False


def _label(row: list[str]) -> str:
    """Texte du séparateur = concaténation de ses cellules non vides."""
    parts = [str(c).strip() for c in row if str(c).strip()]
    return " ".join(parts)


def split_into_zones(path: str) -> tuple[list[dict], dict]:
    """
    Découpe le CSV en zones.

    Retourne (zones, rapport).
    zones = [{"index", "separateur", "header", "rows"}], une entrée par zone non vide.
    rapport = {"delimiteur", "lignes_totales", "header", "nb_zones", "zones": [...]}
    """
    rows, delim = _read_rows(path)
    if not rows:
        return [], {"delimiteur": delim, "lignes_totales": 0, "header": [],
                    "nb_zones": 0, "zones": []}

    h_idx = _pick_header(rows)
    header = rows[h_idx]
    header_len = len(header)

    zones: list[dict] = []
    current_label = ""        # zone initiale (avant tout séparateur)
    current_rows: list[list[str]] = []

    def _flush():
        if current_rows:
            zones.append({
                "index": len(zones) + 1,
                "separateur": current_label,
                "header": header,
                "rows": list(current_rows),
            })

    for r in rows[h_idx + 1:]:
        if is_separator(r, header_len):
            # rupture : on clôt la zone courante et on démarre la suivante
            _flush()
            current_label = _label(r)
            current_rows = []
        else:
            current_rows.append(r)
    _flush()

    rapport = {
        "delimiteur":     delim,
        "lignes_totales": len(rows),
        "header":         header,
        "nb_zones":       len(zones),
        "zones": [
            {"index": z["index"], "separateur": z["separateur"], "lignes": len(z["rows"])}
            for z in zones
        ],
    }
    return zones, rapport


def _eff_len(row: list[str]) -> int:
    """Longueur de la ligne en ignorant les cellules vides de fin."""
    j = len(row)
    while j > 0 and not str(row[j - 1]).strip():
        j -= 1
    return j


def realign_rows(rows: list[list[str]], fallback_header: list[str]) -> tuple[list[str], list[list[str]]]:
    """
    Réaligne les lignes d'une zone à une largeur commune, de façon NON destructive :
    largeur = la plus grande largeur utile rencontrée dans la zone (aucune donnée
    perdue), les lignes plus courtes sont complétées par des cellules vides.
    Retourne (header, rows_normalisées).

    L'utilisateur finalise le détail des décalages dans l'éditeur de la zone.
    """
    effs = [_eff_len(r) for r in rows]
    width = max([e for e in effs if e > 0] + [len(fallback_header), 1])

    base = list(fallback_header)
    # en-têtes : on garde le libellé d'origine quand il existe, sinon ColN
    # (numérotation par position absolue), puis on rend uniques et non vides.
    seen: dict[str, int] = {}
    clean: list[str] = []
    for i in range(width):
        h = str(base[i]).strip() if i < len(base) else ""
        h = h or f"Col{i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        clean.append(h)

    norm = [list(r)[:width] + [""] * max(0, width - len(r)) for r in rows]
    return clean, norm


def write_zones(zones: list[dict], output_dir: str) -> dict[str, str]:
    """
    Écrit une zone par fichier : zone_01.csv, zone_02.csv…
    En-tête répété en tête de chaque fichier ; colonnes d'origine préservées.
    Sortie EBP-friendly : séparateur ';', utf-8-sig, fin de ligne CRLF.
    Retourne {nom_fichier: chemin}.
    """
    os.makedirs(output_dir, exist_ok=True)
    produced: dict[str, str] = {}
    for z in zones:
        fname = f"zone_{z['index']:02d}.csv"
        path = os.path.join(output_dir, fname)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\r\n")
            w.writerow(z["header"])
            w.writerows(z["rows"])
        produced[fname] = path
    return produced
