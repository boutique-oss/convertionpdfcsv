import asyncio
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_executor = ThreadPoolExecutor(max_workers=4)

from core.extractor import extract_pages
from core.agent import extract_articles
from core.csv_writer import to_csv

load_dotenv()

app = FastAPI(title="PDF → CSV Agent")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/extract")
async def extract(
    pdf: UploadFile = File(...),
    page_from: int = Form(1),
    page_to: int = Form(10),
    margin_pct: float = Form(30.0),
    default_unit: str = Form("Pièce"),
    password: str = Form(""),
):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Le fichier doit être un PDF.")
    if page_to > 300:
        raise HTTPException(status_code=400, detail="La limite est de 300 pages maximum.")
    if page_from > page_to:
        raise HTTPException(status_code=400, detail="La page de début doit être ≤ à la page de fin.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(pdf.file, tmp)
        pdf_path = tmp.name

    try:
        loop = asyncio.get_event_loop()
        pages = await loop.run_in_executor(
            _executor, lambda: extract_pages(pdf_path, page_from, page_to, password)
        )
        if not pages:
            raise HTTPException(status_code=422, detail="Aucune page dans la plage indiquée.")

        articles = await loop.run_in_executor(
            _executor, lambda: extract_articles(pages, margin_pct, default_unit)
        )
        if not articles:
            raise HTTPException(status_code=422, detail="Aucun article détecté.")

        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        out.close()
        to_csv(articles, out.name, margin_pct)

        return FileResponse(
            out.name,
            filename="articles.csv",
            media_type="text/csv; charset=utf-8",
            headers={"X-Article-Count": str(len(articles))},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        os.unlink(pdf_path)


app.mount("/static", StaticFiles(directory="static"), name="static")
