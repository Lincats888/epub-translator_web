"""PDF handler using BabelDOC (Intermediate Representation) for layout-perfect translation.

BabelDOC 0.6.2 — uses a three-stage pipeline with an Intermediate Representation (IL):

  PDF → [Frontend] → IL → [Midend: layout + translation] → IL → [Backend] → PDF

The IL is a true document tree: Document → Pages → Paragraphs → Lines/Formulas.
This is the only open-source pipeline whose architecture matches O.Translator /
PDFelement — it operates on a structured document model, not on PDF drawing
instructions, so it can achieve true 1:1 layout preservation.

┌─ Integration notes ──────────────────────────────────────────────┐
│ This file is *standalone* — it does NOT modify any existing      │
│ handler. To use it in your project:                              │
│                                                                  │
│   1. pip install BabelDOC                                        │
│   2. Copy this file to handlers/pdf_babeldoc_handler.py          │
│   3. Add UI option in server/index.html                          │
│   4. Add route in server/server.py                               │
│                                                                  │
│ See BabelDOC_集成指南.md for full instructions.                  │
│ BabelDOC is AGPL-3.0 licensed.                                   │
└──────────────────────────────────────────────────────────────────┘
"""

import os
import time
import logging
import threading

import fitz  # PyMuPDF — for per-page text detection

from babeldoc.format.pdf.document_il.midend.detect_scanned_file import ScannedPDFError
from .base import BaseHandler, TextFragment

logger = logging.getLogger(__name__)

# ── Page text detection ──────────────────────────────────────────

IMAGE_PAGE_CHAR_THRESHOLD = 100  # pages with <100 chars → image/scan page


def _parse_page_range(pages_str: str) -> set[int]:
    """Parse a page range string like '1-5,8,10-12' into a set of 1-indexed ints."""
    result: set[int] = set()
    if not pages_str or not pages_str.strip():
        return result
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a.strip()), int(b.strip()) + 1))
        else:
            result.add(int(part))
    return result


def _pages_to_range_str(pages: set[int] | list[int]) -> str | None:
    """Convert a set of 1-indexed page numbers to a BabelDOC-compatible range string.

    Returns None if the set is empty.
    """
    if not pages:
        return None
    nums = sorted(set(pages))
    ranges = []
    start = nums[0]
    end = nums[0]
    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(f"{start}" if start == end else f"{start}-{end}")
            start = end = n
    ranges.append(f"{start}" if start == end else f"{start}-{end}")
    return ",".join(ranges)


def _filter_text_pages(
    pdf_path: str,
    selected_pages: set[int] | None = None,
    char_threshold: int = IMAGE_PAGE_CHAR_THRESHOLD,
) -> tuple[str | None, list[int], list[int]]:
    """Scan a PDF page-by-page with PyMuPDF and separate text vs image pages.

    Args:
        pdf_path: Path to the PDF.
        selected_pages: 1-indexed pages the user wants to translate. None = all.
        char_threshold: Pages with fewer extractable chars than this are image pages.

    Returns:
        (babeldoc_pages_str, text_pages, image_pages)
        - babeldoc_pages_str: range string for BabelDOC, or None if no text pages
        - text_pages: list of 1-indexed text-based pages
        - image_pages: list of 1-indexed image-based pages
    """
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        if total == 0:
            return None, [], []

        # Determine which pages to check
        if selected_pages:
            check_pages = {p for p in selected_pages if 1 <= p <= total}
        else:
            check_pages = set(range(1, total + 1))

        text_pages = []
        image_pages = []

        for p in sorted(check_pages):
            page_text = doc[p - 1].get_text().strip()
            if len(page_text) < char_threshold:
                image_pages.append(p)
            else:
                text_pages.append(p)

        pages_str = _pages_to_range_str(text_pages)

        logger.info(
            "Page filter: total=%d, text=%d%s, image=%d%s",
            total,
            len(text_pages),
            f" ({pages_str})" if pages_str else "",
            len(image_pages),
            f" {image_pages}" if image_pages else "",
        )

        return pages_str, text_pages, image_pages
    finally:
        doc.close()

# ── Module-level lazy init ────────────────────────────────────────

_BABELDOC_INITIALIZED = False


def _ensure_babeldoc():
    """Lazy-init BabelDOC (creates cache folder etc)."""
    global _BABELDOC_INITIALIZED
    if _BABELDOC_INITIALIZED:
        return
    from babeldoc.format.pdf.high_level import init as _babeldoc_init
    _babeldoc_init()
    _BABELDOC_INITIALIZED = True


def _get_api():
    """Return BabelDOC's key classes after lazy init."""
    _ensure_babeldoc()

    from babeldoc.format.pdf.high_level import translate, async_translate
    from babeldoc.format.pdf.translation_config import TranslationConfig, TranslateResult, WatermarkOutputMode
    from babeldoc.translator.translator import OpenAITranslator
    from babeldoc.docvision.doclayout import OnnxModel

    return translate, async_translate, TranslationConfig, TranslateResult, WatermarkOutputMode, OpenAITranslator, OnnxModel


# ── Handler ───────────────────────────────────────────────────────

class PdfBabeldocHandler(BaseHandler):
    """PDF handler using BabelDOC's end-to-end IR-based pipeline.

    BabelDOC is a full pipeline (parse → translate → render), so the
    standard extract/rebuild interface doesn't apply.
    Use translate_full() as the one-shot entry point.

    Usage:
        handler = PdfBabeldocHandler()
        output = handler.translate_full(
            file_path="paper.pdf",
            target_lang="zh-CN",
            api_key="sk-xxx",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            bilingual=True,
        )
        print(f"Output: {output}")
    """

    @staticmethod
    def supported_extensions():
        return [".pdf"]

    # ── Standard interface (stubs) ────────────────────────────────

    def extract(self, file_path, skip_tags=None, bilingual=True):
        """Not supported — BabelDOC doesn't expose parse-only publicly.

        Use translate_full() for end-to-end translation instead.
        """
        logger.warning(
            "PdfBabeldocHandler.extract() is not available — "
            "use translate_full() for end-to-end translation."
        )
        return []

    def rebuild(self, file_path, fragments, translations, bilingual,
                target_lang="zh-CN"):
        raise NotImplementedError(
            "PdfBabeldocHandler runs the full pipeline internally. "
            "Use translate_full() instead of extract() + rebuild()."
        )

    # ── Main entry point ──────────────────────────────────────────

    def translate_full(
        self,
        file_path,
        target_lang="zh-CN",
        source_lang="en",
        api_key=None,
        base_url=None,
        model=None,
        bilingual=True,
        output_dir=None,
        progress_callback=None,
        pages=None,
    ):
        """One-shot full translation via BabelDOC's IL pipeline.

        Args:
            file_path: Path to input PDF.
            target_lang: Target language code (e.g. 'zh-CN', 'ja', 'fr').
            source_lang: Source language code (e.g. 'en').
            api_key: API key for the translation service.
            base_url: Base URL for the OpenAI-compatible API
                      (e.g. 'https://api.deepseek.com/v1').
            model: Model name (e.g. 'deepseek-chat', 'gpt-4o-mini').
            bilingual: True → bilingual output (dual-language PDF).
                       False → target-language-only PDF.
            output_dir: Output directory (default: input file's dir).
            progress_callback: Optional callable(stage, pct, msg).
            pages: Page range string (e.g. "1-5,8,10-12"). None = all pages.

        Returns:
            Absolute path to the output PDF.
        """
        # ── 1. Init BabelDOC ──
        if progress_callback:
            progress_callback("init", 0, "Initializing BabelDOC...")

        (
            translate_fn,
            _async_translate_fn,
            TranslationConfig,
            TranslateResult,
            WatermarkOutputMode,
            OpenAITranslator,
            OnnxModel,
        ) = _get_api()

        # BabelDOC has a module-level global rate limiter defaulting to
        # 5 QPS (RateLimiter(5) in translator.py). Our qps=10 below only
        # affects pool_max_workers, not the actual API call rate.
        # Raise it to 10 QPS to double translation throughput.
        from babeldoc.translator.translator import set_translate_rate_limiter
        set_translate_rate_limiter(10)

        # ── 2. Build config ──
        model = model or "deepseek-chat"
        base_url = base_url or "https://api.deepseek.com/v1"

        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(file_path)) or "."

        # Language code mapping (BabelDOC expects specific codes)
        lang_map = {
            "zh-CN": "zh-CN", "zh": "zh-CN", "zh-Hans": "zh-CN",
            "en": "en-US", "en-US": "en-US", "en-GB": "en-US",
            "ja": "ja", "ko": "ko",
            "fr": "fr", "de": "de", "es": "es", "ru": "ru",
            "pt": "pt", "it": "it", "nl": "nl",
        }
        lang_in = lang_map.get(source_lang, source_lang)
        lang_out = lang_map.get(target_lang, target_lang)

        if progress_callback:
            progress_callback("init", 5, f"Downloading layout model (~200MB first time)...")

        # ── Filter image/scan pages ──
        # Parse user's page selection (if any), then check each page with
        # PyMuPDF for extractable text.  Skip non-text (image) pages so
        # BabelDOC only processes text-based pages.
        selected = _parse_page_range(pages) if pages else None
        filtered_pages_str, text_pages, image_pages = _filter_text_pages(
            file_path, selected_pages=selected,
        )

        if not text_pages:
            raise RuntimeError(
                "All pages in this PDF are image-based (no extractable text). "
                "Please use OCR translation for scanned/image PDFs."
            )

        if image_pages and progress_callback:
            progress_callback(
                "init", 5,
                f"Skip {len(image_pages)} image page(s): {image_pages} — "
                f"translating {len(text_pages)} text pages"
            )
        logger.info(
            "BabelDOC page filter: user=%s → text=%s (%d pages), image=%d skip",
            pages or "all", filtered_pages_str, len(text_pages), len(image_pages),
        )

        # Create translator (OpenAI-compatible, works with DeepSeek API)
        translator = OpenAITranslator(
            lang_in,        # source language (positional arg 1)
            lang_out,       # target language (positional arg 2)
            model,          # model name (positional arg 3)
            api_key=api_key,
            base_url=base_url,
        )

        # Create config
        # DocLayoutModel.load_available() is called inside TranslationConfig
        # if doc_layout_model is None — it downloads the ONNX model on first run.
        config = TranslationConfig(
            translator=translator,
            input_file=os.path.abspath(file_path),
            lang_in=lang_in,
            lang_out=lang_out,
            doc_layout_model=None,      # auto-download on first call
            output_dir=os.path.abspath(output_dir),
            pages=filtered_pages_str,   # text-only pages (image pages skipped)
            qps=10,                     # API QPS limit
            # ── Output mode ──
            # bilingual=True  → no_mono=True,  no_dual=False → dual PDF only
            # bilingual=False → no_mono=False, no_dual=True  → mono PDF only
            no_mono=bilingual,
            no_dual=not bilingual,
            # ── Quality ──
            use_alternating_pages_dual=True,   # 交替页面双语（与原生 PDF 相同模式）
            use_side_by_side_dual=False,        # 禁用同页并排（默认 True 会导致中文覆盖英文）
            # ── Watermark ──
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            # ── Keep short lines separate (TOC entries, etc.) ──
            split_short_lines=True,
            # ── Skip BabelDOC's internal scan detection ──
            # We already pre-filter image pages with _filter_text_pages(),
            # so BabelDOC only sees text pages. Disable its redundant check.
            skip_scanned_detection=True,
            # ── Auto-detect text-layer + underlying image PDFs ──
            # For pages where a text layer exists but the real content is
            # in the underlying image (e.g. OCR-overlaid scans), BabelDOC
            # auto-switches to image-based extraction per page.
            auto_enable_ocr_workaround=True,
            # Skip automatic glossary extraction — saves 30-90s of
            # extra LLM API calls before translation starts.
            auto_extract_glossary=False,
        )

        # ── 3. Run translation with live progress ──
        # BabelDOC's internal ProgressMonitor uses complex child/parent
        # threading that doesn't reliably propagate to our callback.
        # Instead, use the proven translate_fn() with a lightweight
        # polling thread that reports progress by elapsed time.

        # Remove leftover output of same input file to prevent file-lock errors
        # Only clean up files related to the current translation, not ALL previous outputs
        _input_stem = os.path.splitext(os.path.basename(file_path))[0]
        _expected_outputs = [
            os.path.join(output_dir, _input_stem + ".dual.pdf"),
            os.path.join(output_dir, _input_stem + ".mono.pdf"),
        ]
        for _fpath in _expected_outputs:
            if os.path.exists(_fpath):
                try:
                    os.remove(_fpath)
                except OSError:
                    pass

        _start_time = [time.time()]
        _poll_stop = [False]

        if progress_callback:
            progress_callback("translate", 1, "开始翻译...")

        def _progress_poller():
            """Background thread: report coarse progress every 2s."""
            _cb = progress_callback  # local ref, may be None
            while not _poll_stop[0] and _cb:
                elapsed = time.time() - _start_time[0]
                if elapsed < 30:
                    pct = max(2, min(10, int(elapsed / 30 * 10)))
                    _cb("translate", pct, f"版面分析 ({pct}%)...")
                elif elapsed < 120:
                    pct = min(80, 10 + int((elapsed - 30) / 90 * 70))
                    _cb("translate", pct, f"AI 翻译 ({pct}%)...")
                else:
                    pct = min(95, 80 + int((elapsed - 120) / 60 * 15))
                    _cb("translate", pct, f"排版渲染 ({pct}%)...")
                for _ in range(20):
                    if _poll_stop[0]:
                        return
                    time.sleep(0.1)

        poll_thread = threading.Thread(target=_progress_poller, daemon=True)
        poll_thread.start()
        _start_time[0] = time.time()

        try:
            result = translate_fn(config)
        except ScannedPDFError:
            # BabelDOC's internal scan detection still fired. Force skip it
            # and retry — we already pre-filtered image pages above.
            logger.warning("BabelDOC ScannedPDFError — forcing skip_scanned_detection=True")
            if progress_callback:
                progress_callback("translate", 5, "Forcing text-only translation (internal scan flag overridden)...")
            config.skip_scanned_detection = True
            result = translate_fn(config)
        except Exception:
            raise
        finally:
            _poll_stop[0] = True

        # ── 4. Determine output path ──
        if bilingual and result.dual_pdf_path:
            out_path = str(result.dual_pdf_path)
        elif not bilingual and result.mono_pdf_path:
            out_path = str(result.mono_pdf_path)
        else:
            # Fallback: construct filename from input
            base, ext = os.path.splitext(file_path)
            suffix = "_babeldoc_bilingual" if bilingual else "_babeldoc"
            out_path = os.path.join(output_dir, f"{os.path.basename(base)}{suffix}{ext}")

        if progress_callback:
            progress_callback("done", 100, f"Done: {out_path}")

        return os.path.abspath(out_path)

    # ── Async version for SSE progress ────────────────────────────

    async def translate_full_async(
        self,
        file_path,
        target_lang="zh-CN",
        source_lang="en",
        api_key=None,
        base_url=None,
        model=None,
        bilingual=True,
        output_dir=None,
        pages=None,
    ):
        """Async version of translate_full() that yields SSE-style progress events.

        Yields dicts with keys:
            type: 'progress_start' | 'progress_update' | 'progress_end' | 'finish' | 'error'
            stage, stage_progress, overall_progress, ...

        The last yielded item (type='finish') has key 'output_path' with the result.
        """
        _ensure_babeldoc()

        from babeldoc.format.pdf.high_level import async_translate as _async_translate
        from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
        from babeldoc.translator.translator import OpenAITranslator

        # Raise global rate limiter from default 5 QPS to 10 QPS
        from babeldoc.translator.translator import set_translate_rate_limiter
        set_translate_rate_limiter(10)

        model = model or "deepseek-chat"
        base_url = base_url or "https://api.deepseek.com/v1"
        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(file_path)) or "."

        lang_map = {
            "zh-CN": "zh-CN", "en": "en-US", "ja": "ja", "ko": "ko",
            "fr": "fr", "de": "de", "es": "es", "ru": "ru",
        }
        lang_in = lang_map.get(source_lang, source_lang)
        lang_out = lang_map.get(target_lang, target_lang)

        # ── Filter image/scan pages ──
        selected = _parse_page_range(pages) if pages else None
        filtered_pages_str, text_pages, image_pages = _filter_text_pages(
            file_path, selected_pages=selected,
        )

        if not text_pages:
            raise RuntimeError(
                "All pages in this PDF are image-based (no extractable text). "
                "Please use OCR translation for scanned/image PDFs."
            )

        if image_pages:
            logger.info(
                "BabelDOC async page filter: user=%s → text=%s (%d pages), image=%d skip",
                pages or "all", filtered_pages_str, len(text_pages), len(image_pages),
            )
            yield {
                "type": "progress_update",
                "stage": "init",
                "stage_progress": 5,
                "overall_progress": 0,
                "step": (
                    f"Skipping {len(image_pages)} image page(s): {image_pages} — "
                    f"translating {len(text_pages)} text pages"
                ),
            }

        translator = OpenAITranslator(lang_in, lang_out, model, api_key=api_key, base_url=base_url)

        config = TranslationConfig(
            translator=translator,
            input_file=os.path.abspath(file_path),
            lang_in=lang_in,
            lang_out=lang_out,
            doc_layout_model=None,
            output_dir=os.path.abspath(output_dir),
            pages=filtered_pages_str,   # text-only pages (image pages skipped)
            qps=10,
            no_mono=bilingual,
            no_dual=not bilingual,
            use_alternating_pages_dual=True,
            use_side_by_side_dual=False,
            split_short_lines=True,
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            skip_scanned_detection=True,
            auto_enable_ocr_workaround=True,
            auto_extract_glossary=False,
        )

        try:
            async for event in _async_translate(config):
                yield event
                if event.get("type") in ("finish", "error"):
                    if event.get("type") == "finish":
                        tr = event.get("translate_result")
                        if tr:
                            out = str(tr.dual_pdf_path if bilingual else tr.mono_pdf_path)
                            yield {"type": "result", "output_path": out}
        except ScannedPDFError:
            # BabelDOC's internal scan detection fired despite pre-filtering.
            # Force skip it and retry.
            logger.warning("BabelDOC async ScannedPDFError — forcing skip_scanned_detection=True")
            yield {
                "type": "progress_update",
                "stage": "init",
                "stage_progress": 5,
                "overall_progress": 0,
                "step": "Forcing text-only translation (internal scan flag overridden)...",
            }
            config.skip_scanned_detection = True
            async for event in _async_translate(config):
                yield event
                if event.get("type") in ("finish", "error"):
                    if event.get("type") == "finish":
                        tr = event.get("translate_result")
                        if tr:
                            out = str(tr.dual_pdf_path if bilingual else tr.mono_pdf_path)
                            yield {"type": "result", "output_path": out}
        except Exception as e:
            logger.exception("BabelDOC async translation failed")
            yield {"type": "error", "error": str(e)}
