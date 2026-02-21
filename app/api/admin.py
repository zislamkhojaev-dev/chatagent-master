"""Админка БЗ: UploadFile, IndexDB, /kb/status, /admin/kb. Basic Auth. ТЗ Б.4."""
import asyncio
import json
import secrets
from datetime import datetime
from pathlib import Path

import aiofiles
import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import settings
from app.services.knowledge_base import load_kb_hash_data, index_pdf_to_qdrant
from app.services.qdrant_store import get_qdrant_client
from app.services.bm25_store import build_bm25

router = APIRouter()
security = HTTPBasic()
INDEX_LOCK = asyncio.Lock()
indexing_in_progress = False

MAX_BYTES = settings.KB_MAX_FILE_SIZE_MB * 1024 * 1024
KB_DIR = settings.KB_DIR
KB_PATH = settings.KB_PATH
KB_BACKUP = KB_DIR / settings.KB_BACKUP_FILENAME
HASH_FILE = settings.KB_DIR / "kb_hash.json"


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password not configured")
    correct_user = secrets.compare_digest(credentials.username.encode("utf-8"), settings.ADMIN_USER.encode("utf-8"))
    correct_pass = secrets.compare_digest(credentials.password.encode("utf-8"), settings.ADMIN_PASSWORD.encode("utf-8"))
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/UploadFile")
async def upload_file(
    file: UploadFile,
    _: None = Depends(verify_admin),
):
    if file.content_type and "pdf" not in file.content_type.lower():
        raise HTTPException(status_code=400, detail="Допустимы только PDF-файлы")
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"Файл слишком большой (макс. {settings.KB_MAX_FILE_SIZE_MB} МБ)")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Файл не является PDF")
    try:
        import io
        pdfplumber.open(io.BytesIO(content))
    except Exception:
        raise HTTPException(status_code=400, detail="PDF повреждён или не читается")
    KB_DIR.mkdir(parents=True, exist_ok=True)
    if KB_PATH.exists():
        backup_content = KB_PATH.read_bytes()
        async with aiofiles.open(KB_BACKUP, "wb") as f:
            await f.write(backup_content)
    try:
        async with aiofiles.open(KB_PATH, "wb") as f:
            await f.write(content)
    except Exception:
        raise HTTPException(status_code=500, detail="Ошибка сохранения файла")
    data = load_kb_hash_data(HASH_FILE) or {}
    data["upload_date"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HASH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return {"status": "success", "name": "KB.pdf", "date": data["upload_date"]}


@router.post("/IndexDB")
async def index_db(request: Request, _: None = Depends(verify_admin)):
    global indexing_in_progress
    if not KB_PATH.exists():
        raise HTTPException(status_code=404, detail="Файл kb/KB.pdf не найден. Сначала загрузите файл")
    if indexing_in_progress:
        raise HTTPException(status_code=409, detail="Индексация уже выполняется")
    client = get_qdrant_client()
    if not client:
        raise HTTPException(status_code=503, detail="Qdrant недоступен")
    indexing_in_progress = True
    try:
        async with INDEX_LOCK:
            redis_client = request.app.state.redis
            chunks, _ = await index_pdf_to_qdrant(redis_client, KB_PATH)
            request.app.state.knowledge_base = chunks
            request.app.state.bm25_index = build_bm25(chunks)
            request.app.state.qdrant_client = get_qdrant_client()
            request.app.state.qdrant_collection = settings.QDRANT_ALIAS
            request.app.state.kb_index_ready = True
            data = load_kb_hash_data(HASH_FILE) or {}
            data["index_date"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            with open(HASH_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        return {"status": "success", "date": data["index_date"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка индексации: {e}")
    finally:
        indexing_in_progress = False


@router.get("/kb/status")
async def kb_status(request: Request, _: None = Depends(verify_admin)):
    data = load_kb_hash_data(HASH_FILE) or {}
    file_exists = KB_PATH.exists()
    file_size_mb = round(KB_PATH.stat().st_size / (1024 * 1024), 2) if file_exists else 0
    index_ready = getattr(request.app.state, "kb_index_ready", False)
    return {
        "file_exists": file_exists,
        "file_name": "KB.pdf",
        "file_size_mb": file_size_mb,
        "upload_date": data.get("upload_date", ""),
        "index_date": data.get("index_date", ""),
        "index_ready": index_ready,
        "indexing_in_progress": indexing_in_progress,
        "backup_exists": KB_BACKUP.exists(),
    }


@router.get("/admin/kb", response_class=HTMLResponse)
async def admin_kb_page(_: None = Depends(verify_admin)):
    return get_admin_kb_html()


def get_admin_kb_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Управление БЗ</title>
  <style>
    body { font-family: sans-serif; max-width: 560px; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.5rem; }
    .date { margin: 1rem 0; color: #555; }
    input[type="file"] { margin: 0.5rem 0; }
    button { margin: 0 0.5rem 0.5rem 0; padding: 0.5rem 1rem; cursor: pointer; }
    .modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); align-items: center; justify-content: center; z-index: 10; }
    .modal.show { display: flex; }
    .modal-content { background: #fff; padding: 1.5rem; border-radius: 8px; }
    .modal-actions { margin-top: 1rem; text-align: right; }
    .toast { position: fixed; bottom: 1rem; right: 1rem; padding: 0.75rem 1rem; border-radius: 6px; z-index: 20; max-width: 320px; }
    .toast.success { background: #2e7d32; color: #fff; }
    .toast.error { background: #c62828; color: #fff; }
  </style>
</head>
<body>
  <h1>Управление базой знаний</h1>
  <p class="date">Дата последнего обновления: <span id="lastDate">—</span></p>
  <div>
    <input type="file" id="file" accept=".pdf,application/pdf">
    <br>
    <button id="btnUpload">Загрузить файл</button>
    <button id="btnIndex">Проиндексировать базу знаний</button>
  </div>
  <div id="modal" class="modal">
    <div class="modal-content">
      <p id="modalText"></p>
      <div class="modal-actions">
        <button id="modalNo">Нет</button>
        <button id="modalYes">Да</button>
      </div>
    </div>
  </div>
  <script>
    const api = (path, opts = {}) => fetch(path, { credentials: 'include', ...opts });
    async function loadStatus() {
      const r = await api('/kb/status');
      const d = await r.json();
      const date = d.index_date || d.upload_date || '—';
      document.getElementById('lastDate').textContent = date;
    }
    loadStatus();
    let pendingAction = null;
    function showModal(text, onYes) {
      document.getElementById('modalText').textContent = text;
      document.getElementById('modal').classList.add('show');
      pendingAction = onYes;
    }
    function hideModal() { document.getElementById('modal').classList.remove('show'); pendingAction = null; }
    document.getElementById('modalYes').onclick = () => { if (pendingAction) pendingAction(); hideModal(); };
    document.getElementById('modalNo').onclick = hideModal;
    function toast(msg, isError) {
      const el = document.createElement('div');
      el.className = 'toast ' + (isError ? 'error' : 'success');
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 4000);
    }
    document.getElementById('btnUpload').onclick = () => {
      const file = document.getElementById('file').files[0];
      if (!file) { toast('Выберите файл', true); return; }
      if (file.size > 20 * 1024 * 1024) { toast('Макс. 20 МБ', true); return; }
      showModal('Загрузить базу знаний?', async () => {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('name', 'KB.pdf');
        const r = await api('/UploadFile', { method: 'POST', body: fd });
        const j = await r.json().catch(() => ({}));
        if (r.ok) {
          toast('База знаний загружена. Проиндексируйте базу знаний!');
          document.getElementById('lastDate').textContent = j.date || '—';
        } else toast(j.detail || 'Ошибка', true);
      });
    };
    document.getElementById('btnIndex').onclick = () => {
      showModal('Проиндексировать базу знаний?', async () => {
        const r = await api('/IndexDB', { method: 'POST' });
        const j = await r.json().catch(() => ({}));
        if (r.ok) {
          toast('База знаний проиндексирована');
          loadStatus();
        } else toast(j.detail || 'Ошибка', true);
      });
    };
  </script>
</body>
</html>"""
