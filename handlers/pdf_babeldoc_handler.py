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

from .base import BaseHandler, TextFragment

logger = logging.getLogger(__name__)

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
            qps=10,                     # API QPS limit
            # ── Output mode ──
            # bilingual=True  → no_mono=True,  no_dual=False → dual PDF only
            # bilingual=False → no_mono=False, no_dual=True  → mono PDF only
            no_mono=bilingual,
            no_dual=not bilingual,
            # ── Quality ──
            use_alternating_pages_dual=True,   # 交替页面双语（与原生 PDF 相同模式）
            use_side_by_side_dual=False,        # 禁用同页并排（默认 True 会导致中文覆盖英文）
            # ── Skip BabelDOC's own scan detection ──
            skip_scanned_detection=True,
            # Skip automatic glossary extraction — saves 30-90s of
            # extra LLM API calls before translation starts.
            auto_extract_glossary=False,
        )

        # ── 3. Run translation with live progress ──
        # BabelDOC's internal ProgressMonitor uses complex child/parent
        # threading that doesn't reliably propagate to our callback.
        # Instead, use the proven translate_fn() with a lightweight
        # polling thread that reports progress by elapsed time.

        # Remove leftover output files to prevent file-lock errors
        for f in os.listdir(output_dir):
            if f.endswith(".dual.pdf") or f.endswith(".mono.pdf"):
                try:
                    os.remove(os.path.join(output_dir, f))
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

        translator = OpenAITranslator(lang_in, lang_out, model, api_key=api_key, base_url=base_url)

        config = TranslationConfig(
            translator=translator,
            input_file=os.path.abspath(file_path),
            lang_in=lang_in,
            lang_out=lang_out,
            doc_layout_model=None,
            output_dir=os.path.abspath(output_dir),
            qps=10,
            no_mono=bilingual,
            no_dual=not bilingual,
            use_alternating_pages_dual=True,
            use_side_by_side_dual=False,
            skip_scanned_detection=True,
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
        except Exception as e:
            logger.exception("BabelDOC async translation failed")
            yield {"type": "error", "error": str(e)}
