#!/usr/bin/env python3
"""EPUB Translator Web Server — FastAPI + SSE

Run:
    uvicorn server.server:app --host 127.0.0.1 --port 8080
"""

import asyncio
import base64
import io
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from xml.etree import ElementTree as ET

import yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse

# ── Import existing EpubTranslator modules (no modifications) ──────────
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from epub_translator.config import Config
from epub_translator.extractor import EpubExtractor
from epub_translator.cache import TranslationCache
from epub_translator.parser import parse_file
from epub_translator.translator import Translator
from epub_translator.rebuilder import inject_line_height, rebuild_epub
from epub_translator.crypto import encrypt, decrypt, is_encrypted

from handlers import get_handler, get_supported_extensions, is_supported
from handlers.pdf_handler import PdfHandler
from languages import get_all_languages, get_lang_name, detect_language, get_system_prompt


def _doc_page_count(path):
    """Helper: count pages in a PDF."""
    try:
        import fitz
        doc = fitz.open(path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 40  # fallback

# ── App & State ────────────────────────────────────────────────────────
app = FastAPI(title="EPUB Translator")

_executor = ThreadPoolExecutor(max_workers=2)
_tasks: dict[str, dict] = {}
_lock = Lock()

TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")


def _update(task_id: str, **kwargs):
    with _lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


# ── EPUB Metadata Reader ──────────────────────────────────────────────

NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
}


def _read_epub_meta(epub_path: str) -> dict:
    """Read EPUB metadata: title, author, cover image, TOC."""
    result = {"title": "", "author": "", "cover": None, "toc": [], "opf_dir": "OEBPS/"}

    with zipfile.ZipFile(epub_path, "r") as zf:
        # 1. Find OPF path from container.xml
        try:
            container = zf.read("META-INF/container.xml")
            root = ET.fromstring(container)
            opf_path = root.find(".//container:rootfile", NS).get("full-path")
        except Exception:
            opf_path = "OEBPS/content.opf"

        opf_dir = os.path.dirname(opf_path)
        result["opf_dir"] = opf_dir + "/" if opf_dir else ""

        # Helper: resolve manifest href to zip path (always forward slashes)
        def _zip_path(href):
            if opf_dir:
                return opf_dir + "/" + href
            return href

        # 2. Parse OPF
        try:
            opf = ET.fromstring(zf.read(opf_path))
        except Exception:
            return result

        # Title & Author
        title_el = opf.find(".//dc:title", NS)
        if title_el is not None and title_el.text:
            result["title"] = title_el.text.strip()
        author_el = opf.find(".//dc:creator", NS)
        if author_el is not None and author_el.text:
            result["author"] = author_el.text.strip()

        # Build manifest dict
        manifest = {}
        for item in opf.findall(".//opf:manifest/opf:item", NS):
            manifest[item.get("id", "")] = item

        # 3. Cover image — try multiple strategies
        cover_item = None

        # Strategy A: EPUB2 <meta name="cover" content="item-id"/>
        for meta in opf.findall(".//opf:meta", NS):
            if meta.get("name") == "cover":
                cid = meta.get("content", "")
                if cid in manifest and "image" in manifest[cid].get("media-type", ""):
                    cover_item = manifest[cid]
                break

        # Strategy B: EPUB3 properties="cover-image"
        if not cover_item:
            for item in manifest.values():
                if "cover-image" in item.get("properties", ""):
                    cover_item = item
                    break

        # Strategy C: filename contains "cover"
        if not cover_item:
            for item in manifest.values():
                href = item.get("href", "")
                if "image" in item.get("media-type", "") and "cover" in href.lower():
                    cover_item = item
                    break

        # Strategy D: first image in manifest
        if not cover_item:
            for item in manifest.values():
                if "image" in item.get("media-type", ""):
                    cover_item = item
                    break

        if cover_item is not None:
            href = cover_item.get("href", "")
            if href:
                try:
                    img_data = zf.read(_zip_path(href))
                    mime = cover_item.get("media-type", "image/jpeg")
                    b64 = base64.b64encode(img_data).decode()
                    result["cover"] = f"data:{mime};base64,{b64}"
                except KeyError:
                    pass

        # 4. TOC — NCX (EPUB2)
        ncx_href = None
        for item in manifest.values():
            if item.get("media-type") == "application/x-dtbncx+xml":
                ncx_href = item.get("href")
                break

        if ncx_href:
            try:
                ncx = ET.fromstring(zf.read(_zip_path(ncx_href)))
                for nav in ncx.findall(".//ncx:navPoint", NS):
                    label = nav.find(".//ncx:text", NS)
                    content = nav.find(".//ncx:content", NS)
                    if label is not None and label.text and content is not None:
                        result["toc"].append({
                            "title": label.text.strip(),
                            "href": content.get("src", ""),
                        })
            except Exception:
                pass

        # 5. TOC — nav.xhtml (EPUB3)
        if not result["toc"]:
            for item in manifest.values():
                if "nav" in item.get("properties", ""):
                    nav_href = item.get("href")
                    if nav_href:
                        try:
                            nav_html = zf.read(_zip_path(nav_href)).decode("utf-8", errors="replace")
                            soup = BeautifulSoup(nav_html, "lxml")
                            for a in soup.find_all("a", href=True):
                                title = a.get_text(strip=True)
                                if title:
                                    result["toc"].append({
                                        "title": title,
                                        "href": a["href"].split("#")[0],
                                    })
                        except Exception:
                            pass
                    break

        # 6. TOC — fallback to spine order
        if not result["toc"]:
            for ref in opf.findall(".//opf:spine/opf:itemref", NS):
                href = manifest.get(ref.get("idref", ""), ET.Element("x")).get("href", "")
                if href and href.endswith((".html", ".xhtml", ".htm")):
                    result["toc"].append({"title": href, "href": href})

    return result


def _read_epub_content(epub_path: str, opf_dir: str, href: str) -> str:
    """Read a chapter's HTML content from EPUB."""
    full_path = os.path.join(opf_dir, href).replace("\\", "/")
    with zipfile.ZipFile(epub_path, "r") as zf:
        try:
            html = zf.read(full_path).decode("utf-8", errors="replace")
        except KeyError:
            return "<p>Content not found.</p>"

    # Extract body content
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    if body:
        return str(body)
    return html


# ── Translation Runner ────────────────────────────────────────────────

def _run_translate(task_id: str, epub_path: str, target_lang: str = "zh-CN"):
    """Background EPUB translation — mirrors main.py cmd_translate logic."""
    try:
        _update(task_id, status="loading", step="Loading configuration...")
        config = Config(CONFIG_PATH)
        config.load()
        if not config.api_key or config.api_key in ("sk-xxxx", "sk-your-api-key-here"):
            _update(task_id, status="error", error="API key not configured. Click settings.")
            return

        _update(task_id, step="Extracting EPUB...")
        extractor = EpubExtractor(epub_path, TEMP_DIR)
        extract_dir = extractor.extract()
        book_name = os.path.splitext(os.path.basename(epub_path))[0]

        cache = TranslationCache(extract_dir)
        cache.load()

        content_files = extractor.list_content_files()
        toc_file = extractor.find_toc_file()
        if toc_file and toc_file not in content_files:
            content_files.append(toc_file)
        opf_file = extractor.get_opf_path()
        if opf_file not in content_files:
            content_files.append(opf_file)

        if not content_files:
            _update(task_id, status="error", error="No content files found in EPUB")
            return

        total_files = len(content_files)
        translator_obj = Translator(config)
        total_translated = 0
        total_cached = 0
        is_bilingual = config.translation_mode == "bilingual"

        _update(task_id, status="translating", step="Translating...",
                current_file=0, total_files=total_files,
                file_progress=0, file_total=0)

        for file_idx, file_path in enumerate(content_files, 1):
            # Check stop flag
            if _tasks.get(task_id, {}).get("stopped"):
                _update(task_id, status="stopped", step="Stopped by user")
                return

            rel_path = os.path.relpath(file_path, extract_dir)
            parsed = parse_file(file_path, config.skip_tags, bilingual=is_bilingual)
            if not parsed.fragments:
                continue

            uncached_indices = []
            uncached_texts = []
            for i, frag in enumerate(parsed.fragments):
                cached = cache.get(frag.text)
                if cached is not None:
                    total_cached += 1
                else:
                    uncached_texts.append(frag.text)
                    uncached_indices.append(i)

            if uncached_texts:
                _update(task_id, current_file=file_idx, total_files=total_files,
                        file_name=rel_path, file_progress=0,
                        file_total=len(uncached_texts))

                def on_progress(done, _ft=len(uncached_texts), _fi=file_idx,
                                _tf=total_files, _rn=rel_path):
                    _update(task_id, current_file=_fi, total_files=_tf,
                            file_name=_rn, file_progress=min(done, _ft),
                            file_total=_ft)

                translations_result = translator_obj.translate_all(
                    uncached_texts, progress_callback=on_progress
                )

                file_translations = [None] * len(parsed.fragments)
                for i, frag in enumerate(parsed.fragments):
                    cached = cache.get(frag.text)
                    if cached is not None:
                        file_translations[i] = cached
                for idx, translation in zip(uncached_indices, translations_result):
                    file_translations[idx] = translation
                    cache.put(parsed.fragments[idx].text, translation)
                    total_translated += 1

                cache.flush()
            else:
                file_translations = [cache.get(frag.text) for frag in parsed.fragments]

            parsed.save(file_translations)

        _update(task_id, step="Rebuilding EPUB...")
        if is_bilingual:
            inject_line_height(extract_dir)
        output_path = rebuild_epub(extract_dir, OUTPUT_DIR, book_name)

        _update(task_id, status="done", step="Complete!",
                output=output_path, translated=total_translated, cached=total_cached)

    except Exception as e:
        _update(task_id, status="error", error=str(e))


def _run_translate_generic(task_id: str, file_path: str, target_lang: str = "zh-CN",
                           bilingual: bool = True, pdf_method: str = "pdf2zh"):
    """Background translation for DOCX/PDF using handlers."""
    try:
        # ── pdf2zh fast path: delegates entirely to PDFMathTranslate ──
        if pdf_method == "pdf2zh" and PdfHandler.is_pdf2zh_available():
            _update(task_id, status="translating", step="Translating with PDFMathTranslate...",
                    current_file=0, total_files=1,
                    file_name=os.path.basename(file_path),
                    file_progress=0, file_total=40)
            try:
                # Run pdf2zh in background thread with heartbeat progress
                import threading
                result_holder = [None]
                error_holder = [None]
                done_flag = threading.Event()
                heartbeat_stop = threading.Event()

                def _run_pdf2zh():
                    try:
                        result_holder[0] = PdfHandler.rebuild_via_pdf2zh(
                            file_path, output_dir=os.path.dirname(file_path),
                            vfont="", vchar="")
                    except Exception as e:
                        error_holder[0] = e
                    done_flag.set()

                def _heartbeat():
                    elapsed = 0
                    while not heartbeat_stop.is_set():
                        heartbeat_stop.wait(1.5)
                        elapsed += 1.5
                        # Simulated progress: estimate ~3s per page, cap at 95%
                        pages = _doc_page_count(file_path)
                        est_total = max(pages * 3, 10)
                        pct = min(int(elapsed / est_total * 100), 95)
                        _update(task_id, file_progress=pct, file_total=100,
                                step=f"PDFMathTranslate: {min(pct, 95)}%...")

                t_work = threading.Thread(target=_run_pdf2zh)
                t_beat = threading.Thread(target=_heartbeat)
                t_work.start()
                t_beat.start()
                done_flag.wait()
                heartbeat_stop.set()
                t_beat.join()
                t_work.join()

                if error_holder[0]:
                    _update(task_id, status="error",
                            error=f"PDFMathTranslate failed: {error_holder[0]}")
                    return

                output_path = result_holder[0]
            except Exception as e:
                _update(task_id, status="error", error=f"PDFMathTranslate failed: {e}")
                return

            # Move output to output/ alongside other translated files
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            final_name = os.path.basename(output_path)
            final_path = os.path.join(OUTPUT_DIR, final_name)
            shutil.move(output_path, final_path)
            output_path = final_path

            _update(task_id, file_progress=100, file_total=100)
            _update(task_id, status="done",
                    translated=_doc_page_count(output_path) // 2, cached=0,
                    output=output_path, step="Done!")
            return

        _update(task_id, status="loading", step="Loading configuration...")
        config = Config(CONFIG_PATH)
        config.load()
        if not config.api_key or config.api_key in ("sk-xxxx", "sk-your-api-key-here"):
            _update(task_id, status="error", error="API key not configured. Click settings.")
            return

        _update(task_id, step="Extracting document...")
        handler = get_handler(file_path)
        if handler is None:
            _update(task_id, status="error", error="Unsupported file format.")
            return

        fragments = handler.extract(file_path, bilingual=bilingual)

        if not fragments:
            _update(task_id, status="error", error="No translatable content found.")
            return

        # Source language detection
        sample_text = " ".join(f.text for f in fragments[:5])
        source_lang = detect_language(sample_text)
        _update(task_id, source_lang=source_lang,
                target_lang_name=get_lang_name(target_lang, "zh"))

        # Check if source == target
        from languages.detector import is_same_language
        if is_same_language(sample_text, target_lang):
            _update(task_id, status="error",
                    error=f"Source language ({source_lang}) matches target ({target_lang}). Nothing to translate.")
            return

        # Translate concurrently for speed
        total = len(fragments)
        translator_obj = Translator(config)

        _update(task_id, status="translating", step="Translating...",
                current_file=0, total_files=1,
                file_name=os.path.basename(file_path),
                file_progress=0, file_total=total)

        texts = [f.text for f in fragments]
        progress_state = {"count": 0}

        def on_progress(done):
            progress_state["count"] = min(done, total)
            _update(task_id, file_progress=progress_state["count"], file_total=total)

        all_translations = translator_obj.translate_all(
            texts, progress_callback=on_progress)

        # Retry English-only lines concurrently
        import re
        en_idx = [fi for fi in range(len(all_translations))
                  if not re.search(r'[一-鿿]', str(all_translations[fi]))]
        if en_idx:
            en_texts = [fragments[fi].text for fi in en_idx]
            try:
                retrans = translator_obj.translate_all(en_texts)
                retried = 0
                for fi, trans in zip(en_idx, retrans):
                    if re.search(r'[一-鿿]', str(trans)):
                        all_translations[fi] = trans
                        retried += 1
                if retried:
                    _update(task_id, file_progress=total)
            except Exception:
                pass

        _update(task_id, step="Rebuilding document...")
        output_path = handler.rebuild(
            file_path, fragments, all_translations,
            bilingual=bilingual, target_lang=target_lang,
            method=pdf_method
        )

        _update(task_id, status="done", step="Complete!",
                output=output_path, translated=total, cached=0)

    except Exception as e:
        _update(task_id, status="error", error=str(e))


# ── API Routes ─────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not is_supported(file.filename):
        exts = ", ".join(get_supported_extensions())
        raise HTTPException(400, f"Unsupported format. Supported: {exts}")

    task_id = uuid.uuid4().hex[:12]
    upload_dir = os.path.join(TEMP_DIR, "_uploads")
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = file.filename
    max_name_len = 80
    if len(safe_name) > max_name_len:
        base, ext = os.path.splitext(safe_name)
        safe_name = base[:max_name_len - len(ext)] + ext
    file_path = os.path.join(upload_dir, f"{task_id}_{safe_name}")

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Read metadata based on file type
    ext = os.path.splitext(file.filename)[1].lower()
    if ext == ".epub":
        try:
            meta = _read_epub_meta(file_path)
        except Exception:
            meta = {"title": file.filename, "author": "", "cover": None, "toc": []}
    else:
        meta = {"title": file.filename, "author": "", "cover": None, "toc": []}

    _tasks[task_id] = {
        "status": "loaded", "step": "Ready", "task_id": task_id,
        "filename": file.filename, "file_path": file_path,
        "file_type": ext,
        "opf_dir": meta.get("opf_dir", "OEBPS/"),
        "start_time": time.time(),
    }

    return {
        "task_id": task_id,
        "filename": file.filename,
        "file_type": ext,
        "title": meta.get("title", file.filename),
        "author": meta.get("author", ""),
        "cover": meta.get("cover"),
        "toc": meta.get("toc", []),
    }


@app.get("/api/book/{task_id}/content")
async def book_content(task_id: str, path: str = Query(...)):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    epub_path = task.get("epub_path")
    if not epub_path or not os.path.exists(epub_path):
        raise HTTPException(404, "EPUB file not found")
    opf_dir = task.get("opf_dir", "OEBPS/")
    html = _read_epub_content(epub_path, opf_dir, path)
    return {"html": html}


@app.post("/api/start/{task_id}")
async def start_translation(task_id: str, body: dict = None):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("status") == "translating":
        raise HTTPException(400, "Already translating")
    file_path = task.get("file_path") or task.get("epub_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(404, "File not found")

    # Get target language and mode from request body
    target_lang = "zh-CN"
    bilingual = True
    pdf_method = "pdf2zh"  # PDF always uses pdf2zh engine
    if body:
        target_lang = body.get("target_lang", target_lang)
        bilingual = body.get("bilingual", True)
    print(f"[DEBUG] pdf_method={pdf_method} file_type={task.get('file_type')} bilingual={bilingual}")

    _update(task_id, status="queued", step="Starting...", stopped=False,
            target_lang=target_lang, bilingual=bilingual)

    file_type = task.get("file_type", ".epub")
    if file_type == ".epub":
        _executor.submit(_run_translate, task_id, file_path, target_lang)
    else:
        _executor.submit(_run_translate_generic, task_id, file_path,
                         target_lang, bilingual, pdf_method)

    return {"ok": True}


@app.post("/api/stop/{task_id}")
async def stop_translation(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("status") not in ("translating", "queued", "loading"):
        raise HTTPException(400, "Not running")
    _update(task_id, stopped=True)
    return {"ok": True}


@app.get("/api/progress/{task_id}")
async def progress_stream(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(404, "Task not found")

    async def event_gen():
        last_data = None
        while True:
            with _lock:
                task = dict(_tasks.get(task_id, {}))
            data = json.dumps(task, ensure_ascii=False)
            if data != last_data:
                yield f"data: {data}\n\n"
                last_data = data
            if task.get("status") in ("done", "error", "stopped"):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/download/{task_id}")
async def download(task_id: str):
    task = _tasks.get(task_id)
    if not task or task.get("status") != "done":
        raise HTTPException(404, "File not ready")
    output = task.get("output")
    if not output or not os.path.exists(output):
        raise HTTPException(404, "Output file not found")
    return FileResponse(output, filename=os.path.basename(output), media_type="application/epub+zip")


# ── Language API ────────────────────────────────────────────────────

@app.get("/api/languages")
async def list_languages(ui: str = Query(default="zh")):
    return get_all_languages(ui)


@app.get("/api/formats")
async def list_formats():
    return {"extensions": get_supported_extensions()}


# ── Config API ─────────────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    """Mask API key for display. Handles both plain and encrypted keys."""
    if is_encrypted(key):
        key = decrypt(key)
    if not key or len(key) < 8:
        return ""
    return key[:6] + "****" + key[-4:]


@app.get("/api/config")
async def get_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}

    raw_key = cfg.get("api_key", "")
    encrypted = is_encrypted(raw_key)
    # Decrypt if needed, then check if it's a real key (not placeholder)
    plain_key = decrypt(raw_key) if encrypted else raw_key
    has_key = bool(plain_key and plain_key not in ("sk-xxxx", "sk-your-api-key-here"))

    return {
        "api_key_masked": _mask_key(raw_key) if has_key else "",
        "api_key_set": has_key,
        "api_key_encrypted": encrypted,
        "api_base": cfg.get("api_base", ""),
        "model": cfg.get("model", ""),
        "translation_mode": cfg.get("translation_mode", "bilingual"),
    }


@app.post("/api/config")
async def update_config(body: dict):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}

    if "api_key" in body:
        raw_key = body["api_key"].strip()
        # Encrypt the key before storing
        cfg["api_key"] = encrypt(raw_key) if raw_key else ""
    if "api_base" in body:
        cfg["api_base"] = body["api_base"].strip()
    if "model" in body:
        cfg["model"] = body["model"].strip()
    if "translation_mode" in body:
        cfg["translation_mode"] = body["translation_mode"]

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    return {"ok": True, "api_key_masked": _mask_key(cfg.get("api_key", ""))}


# ── Frontend ───────────────────────────────────────────────────────────

from fastapi.staticfiles import StaticFiles

_images_dir = os.path.join(os.path.dirname(__file__), "images")
if os.path.isdir(_images_dir):
    app.mount("/images", StaticFiles(directory=_images_dir), name="images")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/guide", response_class=HTMLResponse)
async def guide():
    guide_path = os.path.join(os.path.dirname(__file__), "guide.html")
    with open(guide_path, "r", encoding="utf-8") as f:
        return f.read()
