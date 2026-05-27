---
title: PDF CSV Agent
emoji: 📄
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
---

# PDF → CSV Agent

Extrait les articles d'un catalogue PDF et génère un fichier CSV tarifaire prêt à l'import.

## Fonctionnement

1. Chargez un PDF de catalogue fournisseur
2. Sélectionnez la plage de pages à analyser
3. Renseignez votre taux de marge et l'unité par défaut
4. Cliquez **Extraire → CSV** — l'IA (Claude Sonnet) analyse le texte et produit le CSV

Le CSV contient : `Code article`, `Libellé`, `PV HT` (calculé depuis le prix TTC et la marge), `Unité`.

## Installation

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Créez un fichier `.env` à partir de `.env.example` et renseignez votre clé API Anthropic :

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Lancement

```bash
python app.py
```

L'interface Gradio s'ouvre sur `http://localhost:7860`.

## Structure

```
core/
  extractor.py   # extraction texte PDF (PyMuPDF)
  agent.py       # appel Claude Sonnet → JSON articles
  csv_writer.py  # calcul PV HT + export CSV (pandas)
app.py           # interface Gradio
docs/            # GitHub Pages landing
```

## Formule de calcul

```
PV HT = Prix TTC / (1 + marge%)
```

Exemple avec marge 30 % : `100 € TTC → 76,92 € HT`

## Licence

MIT
