---
title: Nettoyeur CSV
emoji: 🧹
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# Nettoyeur CSV

Charge un fichier CSV, choisis les opérations de nettoyage et récupère un fichier propre.
100 % automatique, sans IA ni clé API.

## Opérations

- **Lignes & colonnes vides** — supprime les lignes/colonnes entièrement vides
- **Doublons** — supprime les lignes identiques en double
- **Espaces superflus** — nettoie les espaces en début/fin et les doubles espaces
- **En-têtes propres** — nettoie et dé-duplique les noms de colonnes
- **Virgule décimale (FR)** — convertit `1234.56` → `1234,56` et retire les séparateurs de milliers
- **Format EBP** — sortie séparateur `;`, encodage UTF-8 BOM, fins de ligne Windows (`\r\n`)

L'encodage (UTF-8, CP1252, Latin-1…) et le séparateur (`;`, `,`, tab, `|`) du fichier
d'entrée sont détectés automatiquement.

## Installation

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Lancement

```bash
uvicorn app:app --host 0.0.0.0 --port 7860
```

Puis ouvre `http://localhost:7860`.

## Structure

```
core/
  cleaner.py   # détection encodage/séparateur + nettoyage pandas
app.py         # API FastAPI (/clean, /download)
static/        # interface web
```

## Licence

MIT
