import pandas as pd

def to_csv(articles: list[dict], output_path: str, brand: str = "") -> str:
    """Write articles to a semicolon-separated CSV compatible with EBP Gestion Commerciale."""
    rows = []
    for a in articles:
        row = {
            "Référence": a.get("code", ""),
            "Désignation": a.get("libelle", ""),
            "Prix TTC": float(a.get("prix_ttc") or 0),
            "Unité": a.get("unite", ""),
            "Marque": brand,
        }
        rows.append(row)
    columns = ["Référence", "Désignation", "Prix TTC", "Unité", "Marque"]
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
    return output_path
