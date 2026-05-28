import pandas as pd

_COLUMNS = ["Référence", "Désignation", "Prix TTC", "Unité"]


def to_csv(articles: list[dict], output_path: str) -> str:
    """Write articles to a semicolon-separated CSV compatible with EBP Gestion Commerciale."""
    rows = []
    for a in articles:
        rows.append({
            "Référence": a.get("code", ""),
            "Désignation": a.get("libelle", ""),
            "Prix TTC": float(a.get("prix_ttc") or 0),
            "Unité": a.get("unite", ""),
        })
    df = pd.DataFrame(rows, columns=_COLUMNS)
    df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
    return output_path
