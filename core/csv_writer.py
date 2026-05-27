import pandas as pd


def to_csv(articles: list[dict], output_path: str, margin_pct: float = 30.0) -> str:
    """Write articles to a semicolon-separated CSV and return the path."""
    margin = margin_pct / 100
    rows = []
    for a in articles:
        prix_ttc = float(a.get("prix_ttc") or 0)
        pv_ht = round(prix_ttc / (1 + margin), 2) if prix_ttc else 0.0
        rows.append({
            "Code article": a.get("code", ""),
            "Libellé": a.get("libelle", ""),
            "PV HT": pv_ht,
            "Unité": a.get("unite", ""),
        })
    df = pd.DataFrame(rows, columns=["Code article", "Libellé", "PV HT", "Unité"])
    df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
    return output_path
