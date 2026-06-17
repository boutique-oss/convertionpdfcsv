"""
Nettoyeur de fichiers CSV — 100 % automatique, sans IA ni clé API.

Opérations disponibles (toutes optionnelles, activées par drapeau) :
  - drop_empty        : supprime les lignes et colonnes entièrement vides
  - drop_duplicates   : supprime les lignes en double
  - trim_whitespace   : enlève les espaces superflus en début/fin de cellule
  - normalize_headers : nettoie et dé-duplique les en-têtes de colonnes
  - decimal_comma     : convertit les nombres "1234.56" / "1 234,56" → "1234,56"
  - ebp_format        : sortie au format EBP (séparateur ;, utf-8-sig, \\r\\n)

Détection automatique de l'encodage et du séparateur en entrée.

`clean_csv(raw: bytes, options: dict) -> (cleaned_bytes: bytes, report: dict)`
"""
from __future__ import annotations

import csv
import io
import re

import pandas as pd

# ── Détection ────────────────────────────────────────────────────────────────

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DELIMITERS = (";", ",", "\t", "|")


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """Renvoie (texte décodé, nom de l'encodage retenu)."""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # Dernier recours : latin-1 décode toujours
    return raw.decode("latin-1", errors="replace"), "latin-1"


# Séparateur sentinelle : aucun délimiteur dans l'en-tête → fichier mono-colonne.
# On choisit un caractère absent du texte pour que pandas ne découpe rien.
SINGLE_COL = "\x00"


def detect_delimiter(text: str) -> str:
    """
    Devine le séparateur de colonnes en se basant d'abord sur la ligne d'en-tête,
    pour éviter de découper à tort un fichier mono-colonne dont les valeurs
    contiennent des virgules (ex. « 1.234,50 »).
    """
    lines = [l for l in text.splitlines() if l.strip()][:20]
    if not lines:
        return ","
    header = lines[0]
    present = [d for d in DELIMITERS if d in header]
    if not present:
        return SINGLE_COL
    # Parmi les délimiteurs présents dans l'en-tête, on garde celui dont le
    # nombre d'occurrences est le plus constant ligne à ligne.
    def score(d: str) -> tuple[int, int]:
        hc = header.count(d)
        consistent = sum(1 for l in lines if l.count(d) == hc)
        return (consistent, hc)

    return max(present, key=score)


# ── Helpers de normalisation ───────────────────────────────────────────────────

# Nombre : partie entiere simple OU groupee par milliers (espace/point/virgule),
# suivie d'une partie decimale optionnelle. Exclut les dates type 01.02.2024.
_NUM_RE = re.compile(
    r"^\s*[-+]?"
    r"(?:\d{1,3}(?:[ .,]\d{3})+|\d+)"
    r"(?:[.,]\d+)?"
    r"\s*$"
)


def _looks_numeric(value: str) -> bool:
    return bool(_NUM_RE.match(value)) and any(c.isdigit() for c in value)


def _to_decimal_comma(value: str) -> str:
    """Normalise un nombre vers la virgule décimale française, sans séparateur de milliers."""
    v = value.strip().replace(" ", " ")
    # Retire les espaces utilisés comme séparateur de milliers
    v = v.replace(" ", "")
    if "," in v and "." in v:
        # Le dernier symbole rencontré est le séparateur décimal
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "")          # points = milliers
        else:
            v = v.replace(",", "")          # virgules = milliers
            v = v.replace(".", ",")
    else:
        v = v.replace(".", ",")
    return v


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in headers:
        h = (h or "").strip()
        h = re.sub(r"\s+", " ", h)
        if not h:
            h = "colonne"
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


# ── Nettoyage principal ─────────────────────────────────────────────────────────

def clean_csv(raw: bytes, options: dict) -> tuple[bytes, dict]:
    text, enc_in = detect_encoding(raw)
    delim_in = detect_delimiter(text)

    df = pd.read_csv(
        io.StringIO(text),
        sep=delim_in,
        dtype=str,
        keep_default_na=False,
        engine="python",
        on_bad_lines="skip",
    )

    def _sep_label(d: str) -> str:
        return {"\t": "tabulation", SINGLE_COL: "aucun (1 colonne)"}.get(d, d)

    report: dict = {
        "encodage_detecte": enc_in,
        "separateur_detecte": _sep_label(delim_in),
        "lignes_initiales": int(len(df)),
        "colonnes_initiales": int(df.shape[1]),
        "actions": [],
    }

    def log(msg: str):
        report["actions"].append(msg)

    # 1. Espaces superflus
    if options.get("trim_whitespace"):
        df = df.apply(lambda col: col.map(
            lambda x: re.sub(r"\s+", " ", x).strip() if isinstance(x, str) else x))
        log("Espaces superflus supprimés dans toutes les cellules.")

    # 2. En-têtes normalisés
    if options.get("normalize_headers"):
        new_cols = _dedupe_headers([str(c) for c in df.columns])
        if list(df.columns) != new_cols:
            log(f"En-têtes nettoyés ({df.shape[1]} colonnes).")
        df.columns = new_cols

    # 3. Lignes / colonnes vides
    if options.get("drop_empty"):
        before_rows = len(df)
        # Une cellule "vide" = chaîne vide après strip
        mask_nonempty_row = df.apply(
            lambda r: any(str(v).strip() for v in r), axis=1)
        df = df[mask_nonempty_row]
        removed_rows = before_rows - len(df)

        before_cols = df.shape[1]
        nonempty_cols = [c for c in df.columns
                         if df[c].map(lambda x: bool(str(x).strip())).any()]
        df = df[nonempty_cols]
        removed_cols = before_cols - df.shape[1]

        if removed_rows:
            log(f"{removed_rows} ligne(s) vide(s) supprimée(s).")
        if removed_cols:
            log(f"{removed_cols} colonne(s) vide(s) supprimée(s).")

    # 4. Doublons
    if options.get("drop_duplicates"):
        before = len(df)
        df = df.drop_duplicates()
        removed = before - len(df)
        if removed:
            log(f"{removed} ligne(s) en double supprimée(s).")

    # 5. Décimales en virgule française
    if options.get("decimal_comma"):
        converted = 0

        def conv(x):
            nonlocal converted
            if isinstance(x, str) and _looks_numeric(x):
                new = _to_decimal_comma(x)
                if new != x:
                    converted += 1
                return new
            return x

        df = df.apply(lambda col: col.map(conv))
        if converted:
            log(f"{converted} valeur(s) numérique(s) converties en virgule décimale.")

    # ── Écriture ──────────────────────────────────────────────────────────────
    ebp = options.get("ebp_format")
    if ebp or delim_in in ("\t", SINGLE_COL):
        out_sep = ";"          # EBP, tabulation ou mono-colonne → séparateur sûr
    else:
        out_sep = delim_in
    out_enc = "utf-8-sig"
    line_terminator = "\r\n" if ebp else "\n"

    buf = io.StringIO()
    df.to_csv(buf, sep=out_sep, index=False, lineterminator=line_terminator,
              quoting=csv.QUOTE_MINIMAL)
    cleaned = buf.getvalue().encode(out_enc)

    report["lignes_finales"] = int(len(df))
    report["colonnes_finales"] = int(df.shape[1])
    report["separateur_sortie"] = out_sep
    report["encodage_sortie"] = out_enc
    if not report["actions"]:
        report["actions"].append("Aucune modification — le fichier était déjà propre.")

    return cleaned, report
