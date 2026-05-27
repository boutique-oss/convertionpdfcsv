import pandas as pd

# Colonnes alignées sur le format d'import EBP Gestion Commerciale
_COLUMNS = ["Référence", "Désignation", "Prix de vente HT", "Unité"]


def to_csv(articles: list[dict], output_path: str, margin_pct: float = 30.0) -> str:
    """Write articles to a semicolon-separated CSV compatible with EBP Gestion Commerciale."""
    margin = margin_pct / 100
    rows = []
    for a in articles:
        prix_ttc = float(a.get("prix_ttc") or 0)
        pv_ht = round(prix_ttc / (1 + margin), 2) if prix_ttc else 0.0
        rows.append({
            "Référence": a.get("code", ""),
            "Désignation": a.get("libelle", ""),
            "Prix de vente HT": pv_ht,
            "Unité": a.get("unite", ""),
        })
    df = pd.DataFrame(rows, columns=_COLUMNS)
    df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
    return output_path
