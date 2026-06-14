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
    .wizard { display: flex; flex-direction: column; gap: 0.75rem; margin: 1.25rem 0; }
    .step {
      border: 2px solid #ccc; border-radius: 8px; padding: 0.75rem 1rem;
      background: #fafafa; transition: border-color 0.2s, box-shadow 0.2s;
    }
    .step.current { border-color: #1976d2; background: #e3f2fd; box-shadow: 0 0 0 1px #1976d2; }
    .step.done { border-color: #a5d6a7; background: #f1f8f4; }
    .step-head { display: flex; align-items: flex-start; gap: 0.6rem; margin-bottom: 0.5rem; }
    .step-badge {
      flex-shrink: 0; width: 1.6rem; height: 1.6rem; border-radius: 50%;
      background: #757575; color: #fff; font-size: 0.85rem; font-weight: bold;
      display: flex; align-items: center; justify-content: center;
    }
    .step.current .step-badge { background: #1976d2; }
    .step.done .step-badge { background: #2e7d32; font-size: 0; }
    .step.done .step-badge::after { content: '✓'; font-size: 1rem; line-height: 1; }
    .step-title { font-weight: 600; margin: 0; }
    .step-desc { font-size: 0.9rem; color: #555; margin: 0.2rem 0 0 0; }
    input[type="file"] { margin: 0.35rem 0 0 0; max-width: 100%; }
    .actions { margin-top: 0.35rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
    button { margin: 0; padding: 0.5rem 1rem; cursor: pointer; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .hint { font-size: 0.9rem; color: #444; background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 0.65rem 0.75rem; margin: 0.75rem 0; }
    .busy {
      display: none; margin: 1rem 0; padding: 0.75rem; background: #eceff1; border-radius: 8px;
      border: 1px solid #b0bec5;
    }
    .busy.show { display: block; }
    .busy-text { margin: 0.5rem 0 0 0; font-size: 0.95rem; color: #37474f; }
    .busy-track { height: 4px; background: #cfd8dc; border-radius: 2px; overflow: hidden; }
    .busy-bar {
      height: 100%; width: 35%; background: #1976d2; border-radius: 2px;
      animation: indeterminate 1.1s ease-in-out infinite;
    }
    @keyframes indeterminate {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(380%); }
    }
    .result {
      display: none; margin: 1rem 0; padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.95rem;
    }
    .result.show { display: block; }
    .result.ok { background: #e8f5e9; border: 1px solid #a5d6a7; color: #1b5e20; }
    .result.err { background: #ffebee; border: 1px solid #ef9a9a; color: #b71c1c; }
    .modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); align-items: center; justify-content: center; z-index: 10; }
    .modal.show { display: flex; }
    .modal-content { background: #fff; padding: 1.5rem; border-radius: 8px; max-width: 90vw; }
    .modal-actions { margin-top: 1rem; text-align: right; }
    .toast { position: fixed; bottom: 1rem; right: 1rem; padding: 0.75rem 1rem; border-radius: 6px; z-index: 20; max-width: 320px; }
    .toast.success { background: #2e7d32; color: #fff; }
    .toast.error { background: #c62828; color: #fff; }
  </style>
</head>
<body>
  <h1>Управление базой знаний</h1>
  <p class="date">Дата последнего обновления: <span id="lastDate">—</span></p>

  <div class="wizard">
    <div class="step current" id="step1" data-step="1">
      <div class="step-head">
        <span class="step-badge">1</span>
        <div>
          <p class="step-title">Выберите PDF</p>
          <p class="step-desc">Файл базы знаний (не больше 20 МБ).</p>
        </div>
      </div>
      <input type="file" id="file" accept=".pdf,application/pdf">
    </div>
    <div class="step" id="step2" data-step="2">
      <div class="step-head">
        <span class="step-badge">2</span>
        <div>
          <p class="step-title">Загрузите файл на сервер</p>
          <p class="step-desc">Отправка сохраняет документ как KB.pdf.</p>
        </div>
      </div>
      <div class="actions">
        <button type="button" id="btnUpload">Загрузить файл</button>
      </div>
    </div>
    <div class="step" id="step3" data-step="3">
      <div class="step-head">
        <span class="step-badge">3</span>
        <div>
          <p class="step-title">Проиндексируйте базу знаний</p>
          <p class="step-desc">Без этого шага бот не будет искать ответы в документе (1–3 мин).</p>
        </div>
      </div>
      <div class="actions">
        <button type="button" id="btnIndex">Проиндексировать базу знаний</button>
      </div>
    </div>
  </div>

  <p class="hint"><strong>Важно:</strong> после успешной загрузки обязательно нажмите «Проиндексировать базу знаний». Пока идёт индексация, не закрывайте страницу.</p>

  <div id="busy" class="busy" aria-live="polite">
    <div class="busy-track"><div class="busy-bar"></div></div>
    <p class="busy-text" id="busyText"></p>
  </div>
  <div id="result" class="result" role="status"></div>

  <div id="modal" class="modal">
    <div class="modal-content">
      <p id="modalText"></p>
      <div class="modal-actions">
        <button type="button" id="modalNo">Нет</button>
        <button type="button" id="modalYes">Да</button>
      </div>
    </div>
  </div>
  <script>
    const api = (path, opts = {}) => fetch(path, { credentials: 'include', ...opts });
    const MAX_BYTES = 20 * 1024 * 1024;

    function formatDetail(detail) {
      if (detail == null || detail === '') return 'Произошла ошибка';
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) return detail.map(function (x) { return x.msg || JSON.stringify(x); }).join('. ');
      return String(detail);
    }

    function setBusy(on, text) {
      var b = document.getElementById('busy');
      var t = document.getElementById('busyText');
      var btns = [document.getElementById('btnUpload'), document.getElementById('btnIndex'), document.getElementById('modalYes'), document.getElementById('modalNo')];
      if (on) {
        b.classList.add('show');
        t.textContent = text || '';
        btns.forEach(function (btn) { btn.disabled = true; });
      } else {
        b.classList.remove('show');
        t.textContent = '';
        btns.forEach(function (btn) { btn.disabled = false; });
      }
    }

    function showResult(ok, html) {
      var el = document.getElementById('result');
      el.className = 'result show ' + (ok ? 'ok' : 'err');
      el.innerHTML = html;
    }

    function clearResult() {
      var el = document.getElementById('result');
      el.className = 'result';
      el.innerHTML = '';
    }

    function setStepUI(n, state) {
      var el = document.getElementById('step' + n);
      el.classList.remove('current', 'done');
      if (state === 'current') el.classList.add('current');
      if (state === 'done') el.classList.add('done');
    }

    function syncStepsFromServer(d) {
      [1, 2, 3].forEach(function (i) { setStepUI(i, ''); });
      if (d.file_exists) {
        setStepUI(1, 'done');
        setStepUI(2, 'done');
        setStepUI(3, d.index_ready ? 'done' : 'current');
      } else {
        setStepUI(1, 'current');
      }
    }

    function onFileChosen() {
      var file = document.getElementById('file').files[0];
      clearResult();
      if (!file) {
        loadStatus();
        return;
      }
      setStepUI(1, 'current');
      setStepUI(2, '');
      setStepUI(3, '');
    }

    async function loadStatus() {
      var r = await api('/kb/status');
      var d = await r.json();
      var date = d.index_date || d.upload_date || '—';
      document.getElementById('lastDate').textContent = date;
      syncStepsFromServer(d);
    }

    document.getElementById('file').addEventListener('change', onFileChosen);

    loadStatus();

    var pendingAction = null;
    function showModal(text, onYes) {
      document.getElementById('modalText').textContent = text;
      document.getElementById('modal').classList.add('show');
      pendingAction = onYes;
    }
    function hideModal() {
      document.getElementById('modal').classList.remove('show');
      pendingAction = null;
    }
    document.getElementById('modalYes').onclick = function () {
      var fn = pendingAction;
      hideModal();
      if (fn) fn();
    };
    document.getElementById('modalNo').onclick = hideModal;

    function toast(msg, isError) {
      var el = document.createElement('div');
      el.className = 'toast ' + (isError ? 'error' : 'success');
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(function () { el.remove(); }, 4000);
    }

    document.getElementById('btnUpload').onclick = function () {
      var file = document.getElementById('file').files[0];
      if (!file) { toast('Сначала выберите PDF (шаг 1)', true); return; }
      if (file.size > MAX_BYTES) { toast('Файл слишком большой (макс. 20 МБ)', true); return; }
      clearResult();
      showModal('Загрузить базу знаний на сервер?', function () {
        setBusy(true, 'Загрузка файла на сервер…');
        var fd = new FormData();
        fd.append('file', file);
        fd.append('name', 'KB.pdf');
        api('/UploadFile', { method: 'POST', body: fd })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { r: r, j: j }; }); })
          .then(function (_ref) {
            var r = _ref.r, j = _ref.j;
            setBusy(false);
            if (r.ok) {
              document.getElementById('lastDate').textContent = j.date || '—';
              setStepUI(1, 'done');
              setStepUI(2, 'done');
              setStepUI(3, 'current');
              showResult(true, '<strong>Файл загружен.</strong> Дата: ' + (j.date || '—') + '. Теперь выполните шаг 3 — проиндексацию.');
              toast('Файл загружен. Не забудьте проиндексировать!');
            } else {
              var msg = formatDetail(j.detail);
              showResult(false, '<strong>Ошибка загрузки.</strong> ' + msg);
              toast(msg, true);
              loadStatus();
            }
          })
          .catch(function () {
            setBusy(false);
            showResult(false, '<strong>Ошибка сети.</strong> Проверьте подключение и попробуйте снова.');
            toast('Ошибка сети', true);
          });
      });
    };

    document.getElementById('btnIndex').onclick = function () {
      clearResult();
      showModal('Запустить индексацию? Это может занять 1–3 минуты.', function () {
        setBusy(true, 'Идёт индексация, не закрывайте страницу… Это может занять несколько минут.');
        api('/IndexDB', { method: 'POST' })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { r: r, j: j }; }); })
          .then(function (_ref2) {
            var r = _ref2.r, j = _ref2.j;
            setBusy(false);
            if (r.ok) {
              setStepUI(1, 'done');
              setStepUI(2, 'done');
              setStepUI(3, 'done');
              showResult(true, '<strong>Индексация завершена.</strong> Дата: ' + (j.date || '—') + '. Бот может отвечать по новой базе.');
              toast('База знаний проиндексирована');
              loadStatus();
            } else {
              var msg2 = formatDetail(j.detail);
              showResult(false, '<strong>Ошибка индексации.</strong> ' + msg2);
              toast(msg2, true);
              loadStatus();
            }
          })
          .catch(function () {
            setBusy(false);
            showResult(false, '<strong>Ошибка сети.</strong> Проверьте подключение и попробуйте снова.');
            toast('Ошибка сети', true);
          });
      });
    };
  </script>
</body>
</html>"""
