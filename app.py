import os
import tempfile
import time
import traceback

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.cleaner import clean_csv

app = FastAPI(title="Nettoyeur CSV")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_CSV_MB = 50

# Résultats temporaires : {download_id: (chemin, nom_origine, timestamp)}
_results: dict[str, tuple[str, str, float]] = {}
_RESULT_TTL = 3600  # 1 h


def _purge_old():
    now = time.time()
    for did, (path, _name, ts) in list(_results.items()):
        if now - ts > _RESULT_TTL:
            try:
                os.unlink(path)
            except OSError:
                pass
            _results.pop(did, None)


@app.get("/ping")
async def ping():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/clean")
async def clean(
    csv_file: UploadFile = File(...),
    drop_empty: bool = Form(True),
    drop_duplicates: bool = Form(True),
    trim_whitespace: bool = Form(True),
    normalize_headers: bool = Form(True),
    keep_digits_only: bool = Form(False),
    decimal_comma: bool = Form(False),
    ebp_format: bool = Form(False),
):
    name = csv_file.filename or "fichier.csv"
    if not name.lower().endswith((".csv", ".txt", ".tsv")):
        raise HTTPException(status_code=400, detail="Le fichier doit être un .csv, .tsv ou .txt.")

    raw = b""
    chunk_size = 1024 * 1024
    while True:
        chunk = await csv_file.read(chunk_size)
        if not chunk:
            break
        raw += chunk
        if len(raw) > MAX_CSV_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"Fichier trop volumineux (max {MAX_CSV_MB} MB).",
            )

    if not raw.strip():
        raise HTTPException(status_code=400, detail="Le fichier est vide.")

    options = {
        "drop_empty": drop_empty,
        "drop_duplicates": drop_duplicates,
        "trim_whitespace": trim_whitespace,
        "normalize_headers": normalize_headers,
        "keep_digits_only": keep_digits_only,
        "decimal_comma": decimal_comma,
        "ebp_format": ebp_format,
    }

    try:
        cleaned, report = clean_csv(raw, options)
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}"
        raise HTTPException(status_code=422, detail=detail)

    _purge_old()
    out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    out.write(cleaned)
    out.close()

    download_id = os.path.splitext(os.path.basename(out.name))[0]
    base, ext = os.path.splitext(os.path.basename(name))
    out_name = f"{base}_nettoye{ext or '.csv'}"
    _results[download_id] = (out.name, out_name, time.time())

    return JSONResponse({"download_id": download_id, "report": report})


@app.get("/download/{download_id}")
async def download(download_id: str):
    entry = _results.get(download_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Résultat expiré ou introuvable.")
    path, out_name, _ts = entry
    return FileResponse(
        path,
        filename=out_name,
        media_type="text/csv; charset=utf-8",
    )


app.mount("/static", StaticFiles(directory="static"), name="static")
