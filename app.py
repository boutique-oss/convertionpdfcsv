import asyncio
import os
import shutil
import tempfile
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.extractor import extract_pages
from core.agent import extract_articles
from core.csv_writer import to_csv

load_dotenv()

app = FastAPI(title="PDF → CSV Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
_executor = ThreadPoolExecutor(max_workers=2)

_jobs: dict[str, dict] = {}

MAX_PDF_MB = 50


def _run_job(job_id: str, pdf_path: str, page_from: int, page_to: int,
             margin_pct: float, default_unit: str, password: str):
    try:
        pages = extract_pages(pdf_path, page_from, page_to, password)
        if not pages:
            _jobs[job_id] = {"status": "error", "error": "Aucune page extraite dans la plage indiquée."}
            return

        articles = extract_articles(pages, margin_pct, default_unit)
        if not articles:
            _jobs[job_id] = {"status": "error", "error": "Aucun article détecté dans ces pages."}
            return

        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        out.close()
        to_csv(articles, out.name, margin_pct)
        _jobs[job_id] = {"status": "done", "result": out.name, "count": len(articles)}

    except Exception as exc:
        _jobs[job_id] = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}",
        }
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


@app.get("/ping")
async def ping():
    return {"ok": True}


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
        raise HTTPException(status_code=400, detail="Limite : 300 pages maximum.")
    if page_from > page_to:
        raise HTTPException(status_code=400, detail="Page de début > page de fin.")

    # Sauvegarde du PDF en lisant par chunks pour éviter l'OOM
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        size = 0
        chunk_size = 1024 * 1024  # 1 MB
        while True:
            chunk = await pdf.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_PDF_MB * 1024 * 1024:
                os.unlink(tmp.name)
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF trop volumineux (max {MAX_PDF_MB} MB)."
                )
            tmp.write(chunk)
        pdf_path = tmp.name

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "processing"}

    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _executor,
        _run_job,
        job_id, pdf_path, page_from, page_to, margin_pct, default_unit, password,
    )

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    if job["status"] == "error":
        return JSONResponse({"status": "error", "error": job["error"]})
    if job["status"] == "done":
        return JSONResponse({"status": "done", "count": job["count"]})
    return JSONResponse({"status": "processing"})


@app.get("/download/{job_id}")
async def download(job_id: str):
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Résultat non disponible.")
    return FileResponse(
        job["result"],
        filename="articles.csv",
        media_type="text/csv; charset=utf-8",
        headers={"X-Article-Count": str(job["count"])},
    )


app.mount("/static", StaticFiles(directory="static"), name="static")
