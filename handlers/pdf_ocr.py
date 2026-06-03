"""PDF OCR module — extracts text from scanned/image-based PDFs.

Uses SiliconFlow's vision API (Qwen3-VL / DeepSeek-OCR) to OCR each page.
Fully isolated from the translation pipeline — call before translation, not during.

Usage:
    ocr = PdfOcr(api_key="sk-xxx", base_url="https://api.siliconflow.com/v1",
                 model="Qwen/Qwen3-VL-32B-Instruct")
    if ocr.is_scanned("scan.pdf"):
        texts = ocr.ocr_pdf("scan.pdf")
        # texts[i] = extracted text of page i
"""

import base64
import io
import logging
import os
from typing import Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────
DEFAULT_OCR_MODEL = "Qwen/Qwen3-VL-32B-Instruct"
DEFAULT_OCR_BASE_URL = "https://api.siliconflow.com/v1"
SCAN_THRESHOLD = 0.70  # >70% pages without text → scanned
PAGE_DPI = 150         # render resolution for OCR (balance quality vs cost)

# ── OCR prompt ────────────────────────────────────────────────────────────
OCR_PROMPT = (
    "请提取这张图片中的所有文字，保持原始段落和换行格式。"
    "对于表格内容，请用制表符或空格对齐。"
    "直接返回文字内容，不要添加任何解释或注释。"
)


class PdfOcrError(Exception):
    """Base exception for OCR failures."""


class PdfOcrConfigError(PdfOcrError):
    """OCR not configured (missing API key, etc.)."""


class PdfOcr:
    """OCR engine for scanned PDFs using a vision-language model API.

    The client is created once per instance and reused across pages.
    All API calls use the OpenAI-compatible chat completions protocol.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_OCR_BASE_URL,
        model: str = DEFAULT_OCR_MODEL,
        enabled: bool = True,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._enabled = enabled
        self._client = None

        if enabled and api_key:
            self._init_client()

    # ── public API ──────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """True if OCR is configured and ready to use."""
        return self._enabled and bool(self._api_key)

    @staticmethod
    def is_scanned(pdf_path: str) -> bool:
        """Detect whether a PDF is a scanned (image-based) document.

        Uses two checks:
        1. More than SCAN_THRESHOLD (70%) of pages have <100 chars of text
        2. Average text per page is less than 200 chars

        Returns:
            True if the PDF appears to be scanned.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        try:
            total = len(doc)
            if total == 0:
                return False

            total_chars = 0
            sparse_pages = 0
            for page in doc:
                text_len = len(page.get_text().strip())
                total_chars += text_len
                if text_len < 100:  # Page with very little extractable text
                    sparse_pages += 1

            sparse_ratio = sparse_pages / total
            avg_chars = total_chars / total if total > 0 else 0

            is_scan = sparse_ratio > SCAN_THRESHOLD or avg_chars < 200
            logger.debug(
                "Scanned detection: sparse=%d/%d (%.0f%%), avg_chars=%.0f, "
                "threshold=%.0f%% => %s",
                sparse_pages, total, sparse_ratio * 100, avg_chars,
                SCAN_THRESHOLD * 100, "SCANNED" if is_scan else "TEXT"
            )
            return is_scan
        finally:
            doc.close()

    def ocr_page(self, image_bytes: bytes) -> str:
        """OCR a single page image.

        Args:
            image_bytes: PNG or JPEG image data.

        Returns:
            Extracted text from the page.

        Raises:
            PdfOcrConfigError: If OCR is not configured.
            PdfOcrError: On API or processing errors.
        """
        if not self.ready:
            raise PdfOcrConfigError(
                "OCR is not configured. Please set OCR API Key in settings."
            )

        img_b64 = base64.b64encode(image_bytes).decode()
        logger.debug("OCR request: %d bytes image → %d chars base64",
                     len(image_bytes), len(img_b64))

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ]
                }],
                max_tokens=4096,
            )
        except Exception as e:
            raise PdfOcrError(f"OCR API call failed: {e}") from e

        text = response.choices[0].message.content or ""
        logger.debug(
            "OCR result: %d chars, %d tokens (prompt=%d, completion=%d)",
            len(text),
            response.usage.total_tokens if response.usage else 0,
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0,
        )
        return text

    def ocr_pdf(
        self,
        pdf_path: str,
        progress_callback=None,
    ) -> list[str]:
        """OCR an entire scanned PDF, page by page.

        Args:
            pdf_path: Path to the scanned PDF file.
            progress_callback: Optional callable(page_idx, total_pages, text).
                Called after each page is processed.

        Returns:
            List of strings, one per page (0-indexed).

        Raises:
            PdfOcrConfigError: If OCR is not configured.
        """
        if not self.ready:
            raise PdfOcrConfigError(
                "OCR is not configured. Please set OCR API Key in settings."
            )

        doc = fitz.open(pdf_path)
        total = len(doc)
        results: list[str] = []

        logger.info("Starting OCR for %s (%d pages)", pdf_path, total)

        for i in range(total):
            page = doc[i]
            # Render page to PNG at configured DPI
            pix = page.get_pixmap(dpi=PAGE_DPI)
            img_bytes = pix.tobytes("png")

            logger.debug("Page %d/%d: rendered %d bytes at %d DPI",
                         i + 1, total, len(img_bytes), PAGE_DPI)

            try:
                text = self.ocr_page(img_bytes)
            except PdfOcrError:
                logger.warning("Page %d/%d OCR failed, using empty text", i + 1, total)
                text = ""

            results.append(text)

            if progress_callback:
                progress_callback(i + 1, total, text)

        doc.close()
        logger.info("OCR complete: %d pages processed", total)
        return results

    # ── internal ────────────────────────────────────────────────────────

    def _init_client(self):
        """Lazy-init the OpenAI-compatible client."""
        from openai import OpenAI
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
