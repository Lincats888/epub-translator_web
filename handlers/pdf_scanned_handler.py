"""Scanned PDF rebuilder — erase original text from page images, overlay translation.

Uses EasyOCR for text block detection (bounding boxes), OpenCV for inpainting
(erase text), and Pillow for drawing translated text. Outputs a new PDF with
the cleaned page images + translation overlays.

Usage:
    rebuilder = PdfScannedRebuilder()
    output_pdf = rebuilder.rebuild(
        input_pdf="scan.pdf",
        translations=[["第1页译文段落1", "段落2"], ["第2页译文段落1"]],
        bilingual=True,
    )
"""

import io
import logging
import os

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
RENDER_DPI = 150          # DPI for page image rendering
INPAINT_RADIUS = 5        # OpenCV inpaint radius (pixels)
MASK_DILATE = 2           # Pixels to expand mask around text for clean erasure


class PdfScannedRebuilder:
    """Rebuild a scanned PDF with translated text overlays.

    Pipeline: render page → detect text blocks → erase original → draw translation

    All image processing is done at RENDER_DPI resolution, then downscaled
    back to PDF point sizes when reconstructing.
    """

    def __init__(self, lang: str = "en"):
        """Initialize OCR reader and font cache.

        Args:
            lang: EasyOCR language code. Use 'ch_sim' for Chinese,
                  'en' for English. Combine with comma: 'en,ch_sim'.
        """
        self._lang = lang
        self._reader = None       # lazy-init
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def detect_all_pages(self, pdf_path: str) -> list[list[dict]]:
        """Detect text blocks on every page of a scanned PDF.

        Returns:
            Per-page lists of block dicts. Each block has:
            bbox, text, confidence, x, y, w, h
        """
        doc = fitz.open(pdf_path)
        result = []
        for page in doc:
            img, _ = self._render_page(page)
            blocks = self._detect_text_blocks(img)
            result.append(blocks)
        doc.close()
        logger.info("Detected text blocks across %d pages", len(result))
        return result

    def detect_blocks_on_image(self, image_bytes: bytes) -> list[dict]:
        """Detect text blocks on a single page image.

        Args:
            image_bytes: PNG or JPEG image data.

        Returns:
            List of block dicts with bbox, text, confidence, x, y, w, h.
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return self._detect_text_blocks(img)

    def rebuild(
        self,
        input_pdf: str,
        translations: list[list[str]],
        bilingual: bool = False,
        progress_callback=None,
    ) -> str:
        """Rebuild a scanned PDF with translated text.

        Args:
            input_pdf: Path to the scanned PDF file.
            translations: Per-page lists of translated text blocks.
                          translations[page_idx] = [block1_trans, block2_trans, ...]
            bilingual: If True, original text is kept; translation placed below.
                       If False, original text is erased first.
            progress_callback: Optional callable(page_idx, total_pages).

        Returns:
            Path to the output PDF file.
        """
        doc = fitz.open(input_pdf)
        total = len(doc)
        out_doc = fitz.open()

        for pi in range(total):
            page = doc[pi]
            page_rect = page.rect

            # 1. Render page to image
            img, scale = self._render_page(page)

            # 2. Detect text blocks + get bboxes
            blocks = self._detect_text_blocks(img)

            if not bilingual:
                # 3. Erase original text
                img = self._erase_text(img, blocks)

            # 4. Overlay translations (or original + translation for bilingual)
            page_trans = translations[pi] if pi < len(translations) else []
            img = self._overlay_translations(img, blocks, page_trans, bilingual, scale)

            # 5. Insert into output PDF
            out_page = out_doc.new_page(width=page_rect.width, height=page_rect.height)
            img_bytes = self._pil_to_png(img)
            out_page.insert_image(page_rect, stream=img_bytes)

            if progress_callback:
                progress_callback(pi + 1, total)

        # Save output
        base, _ = os.path.splitext(input_pdf)
        output_path = f"{base}_translated.pdf"
        out_doc.save(output_path)
        out_doc.close()
        doc.close()

        logger.info("Rebuilt PDF saved: %s (%d pages)", output_path, total)
        return output_path

    # ── Step 1: Render ──────────────────────────────────────────────────

    @staticmethod
    def _render_page(page: fitz.Page) -> tuple[np.ndarray, float]:
        """Render a PDF page to a numpy image array.

        Returns:
            (image as BGR numpy array, scale factor from PDF points to pixels)
        """
        pix = page.get_pixmap(dpi=RENDER_DPI)
        scale = pix.width / page.rect.width  # pixels per PDF point
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img_np = np.array(img.convert("RGB"))
        # Convert RGB to BGR for OpenCV
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        return img_bgr, scale

    # ── Step 2: Detect ──────────────────────────────────────────────────

    def _detect_text_blocks(self, img: np.ndarray) -> list[dict]:
        """Detect text blocks using EasyOCR.

        Returns list of dicts with keys: bbox (4 corner points), text, bbox_rect (x,y,w,h).
        """
        if self._reader is None:
            import easyocr
            self._reader = easyocr.Reader(
                self._lang.split(",") if "," in self._lang else [self._lang],
                gpu=False,
            )

        results = self._reader.readtext(img)
        blocks = []
        for bbox, text, conf in results:
            if not text.strip():
                continue
            # bbox = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (4 corners)
            x_vals = [p[0] for p in bbox]
            y_vals = [p[1] for p in bbox]
            blocks.append({
                "bbox": bbox,
                "text": text.strip(),
                "confidence": conf,
                "x": int(min(x_vals)),
                "y": int(min(y_vals)),
                "w": int(max(x_vals) - min(x_vals)),
                "h": int(max(y_vals) - min(y_vals)),
            })

        logger.debug("Detected %d text blocks", len(blocks))
        return blocks

    # ── Step 3: Erase ───────────────────────────────────────────────────

    @staticmethod
    def _erase_text(img: np.ndarray, blocks: list[dict]) -> np.ndarray:
        """Erase text from image using OpenCV inpainting.

        Builds a binary mask from text bounding boxes, dilates it slightly,
        then uses Telea inpainting to fill erased regions with background.
        """
        if not blocks:
            return img

        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        for blk in blocks:
            pts = np.array([[int(p[0]), int(p[1])] for p in blk["bbox"]], dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)

        if MASK_DILATE > 0:
            kernel = np.ones((MASK_DILATE, MASK_DILATE), np.uint8)
            mask = cv2.dilate(mask, kernel, iterations=1)

        cleaned = cv2.inpaint(img, mask, INPAINT_RADIUS, cv2.INPAINT_TELEA)
        logger.debug("Erased %d text blocks via inpainting", len(blocks))
        return cleaned

    # ── Step 4: Overlay ─────────────────────────────────────────────────

    def _overlay_translations(
        self,
        img: np.ndarray,
        blocks: list[dict],
        translations: list[str],
        bilingual: bool,
        scale: float,
    ) -> np.ndarray:
        """Draw translated text onto the image.

        For each block, determines font size to fit the bbox width,
        then draws the translation at the original text position.

        In bilingual mode, original text is NOT erased and translation
        is drawn just below the original text.
        """
        if not translations:
            return img

        # Convert to PIL for text drawing
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)

        for i, (blk, trans) in enumerate(zip(blocks, translations)):
            if i >= len(translations):
                break
            if not trans:
                continue

            x, y, w, h = blk["x"], blk["y"], blk["w"], blk["h"]

            if bilingual:
                # Translation goes below original (original text NOT erased)
                trans_y = y + h + 4
                avail_w = w
                avail_h = h  # same height as original
            else:
                trans_y = y
                avail_w = w
                avail_h = h

            # Determine font size to fit width
            font_size = self._fit_font_size(trans, avail_w, avail_h)

            # Wrap text if needed
            lines = self._wrap_text_pil(trans, font_size, avail_w)

            # Draw each line
            line_h = int(font_size * 1.3)
            for li, line in enumerate(lines):
                ly = trans_y + li * line_h
                if ly + line_h > trans_y + avail_h:
                    break  # don't overflow bbox
                font = self._get_font(font_size)
                draw.text((x, ly), line, font=font, fill=(0, 0, 0))

        # Convert back to BGR
        result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return result

    # ── Font helpers ────────────────────────────────────────────────────

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get a CJK-capable PIL font at the given size (cached)."""
        if size not in self._font_cache:
            font_path = self._find_cjk_font()
            self._font_cache[size] = ImageFont.truetype(font_path, size)
        return self._font_cache[size]

    @staticmethod
    def _find_cjk_font() -> str:
        """Find a CJK-capable TrueType font on the system."""
        # Windows fonts
        candidates = [
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Linux fonts
        linux_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
        for path in linux_candidates:
            if os.path.exists(path):
                return path
        raise FileNotFoundError(
            "No CJK font found. Install Noto Sans CJK or place simsun.ttc in the fonts directory."
        )

    @staticmethod
    def _fit_font_size(text: str, max_w: int, max_h: int) -> int:
        """Binary search for the largest font size that fits within bbox.

        Uses Pillow textbbox for measurement.
        Returns minimum font size of 8.
        """
        lo, hi = 8, max_h  # font size can't exceed bbox height
        best = lo

        while lo <= hi:
            mid = (lo + hi) // 2
            font = ImageFont.truetype(PdfScannedRebuilder._find_cjk_font(), mid)
            lines = PdfScannedRebuilder._wrap_text_pil(text, mid, max_w)
            line_h = int(mid * 1.3)
            total_h = len(lines) * line_h

            if total_h <= max_h:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        return best

    @staticmethod
    def _wrap_text_pil(text: str, font_size: int, max_width: int) -> list[str]:
        """Wrap text to fit within max_width pixels using Pillow text measurement.

        Handles CJK (char-by-char break) and Latin (word break).
        """
        if not text:
            return [""]

        font = ImageFont.truetype(PdfScannedRebuilder._find_cjk_font(), font_size)
        # Use a temp image for textbbox
        tmp_img = Image.new("RGB", (1, 1))
        tmp_draw = ImageDraw.Draw(tmp_img)

        lines = []
        current = ""

        for ch in text:
            test = current + ch
            bbox = tmp_draw.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]

            if w > max_width and current:
                # Find break point
                is_cjk = '一' <= ch <= '鿿' or '぀' <= ch <= 'ヿ' or '가' <= ch <= '힯'
                if ch.isspace():
                    lines.append(current)
                    current = ""
                elif is_cjk:
                    lines.append(current)
                    current = ch
                else:
                    # Latin: break at last space
                    last_space = current.rfind(" ")
                    if last_space > 0:
                        lines.append(current[:last_space])
                        current = current[last_space + 1:] + ch
                    else:
                        lines.append(current)
                        current = ch
            else:
                current = test

        if current:
            lines.append(current)

        return lines if lines else [text]

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _pil_to_png(img: np.ndarray) -> bytes:
        """Convert numpy BGR image to PNG bytes."""
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return buf.getvalue()
