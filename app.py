"""
Point d'entrée du Space : FastAPI minimal + UI Gradio à onglets servie sur /.

Une seule page, trois onglets :
  - 🧹 Nettoyeur CSV   (core.cleaner — 100 % local, sans IA)
  - 🔎 Analyse         (zones éditables, réalignement, mapping colonnes → EBP)
  - 📦 Conversion EBP  (export 1 CSV par zone : colonnes EBP + Taux de marge, ZIP)

Lancement (Docker) : uvicorn app:app --host 0.0.0.0 --port 7860
"""
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import gradio as gr

from app_gradio import demo

load_dotenv()

app = FastAPI(title="Atelier EBP — Nettoyeur CSV + Extracteur PDF")


@app.get("/ping")
async def ping():
    return JSONResponse({"ok": True})


# UI Gradio (les deux onglets) montée à la racine.
app = gr.mount_gradio_app(app, demo, path="/")
