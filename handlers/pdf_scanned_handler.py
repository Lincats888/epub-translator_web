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
RENDER_DPI = 120          # DPI for page image rendering (balance speed vs accuracy)
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
        self._reader = None       # lazy-init (EasyOCR, kept for fallback)
        self._surya = None        # lazy-init (Surya predictor)
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    # ── Surya detection ────────────────────────────────────────────────

    def _init_surya(self):
        """Lazy-init the Surya line detection model."""
        if self._surya is None:
            from surya.detection import DetectionPredictor
            self._surya = DetectionPredictor()

    def _detect_text_blocks_surya(self, img: np.ndarray) -> list[dict]:
        """Detect text lines using Surya (Transformer-based, paragraph-level).

        Returns the same dict format as EasyOCR for compatibility with
        erase/overlay/merge methods.
        """
        self._init_surya()
        # Convert BGR numpy → PIL Image
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        results = self._surya([pil_img])
        blocks = []
        for item in results[0].bboxes:
            # Surya PolygonBox: .polygon = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            poly = [[int(p[0]), int(p[1])] for p in item.polygon]
            x_vals = [p[0] for p in poly]
            y_vals = [p[1] for p in poly]
            blocks.append({
                "bbox": poly,
                "text": "",  # Surya is detection-only, text from Vision API
                "_easyocr_text": "",
                "confidence": float(item.confidence),
                "x": int(min(x_vals)),
                "y": int(min(y_vals)),
                "w": int(max(x_vals) - min(x_vals)),
                "h": int(max(y_vals) - min(y_vals)),
            })

        logger.debug("Surya detected %d text lines", len(blocks))
        return blocks

    # ── Public API ──────────────────────────────────────────────────────

    def detect_all_pages(self, pdf_path: str, progress_callback=None) -> list[list[dict]]:
        """Detect text blocks on every page of a scanned PDF.

        Args:
            pdf_path: Path to the scanned PDF.
            progress_callback: Optional callable(page_done, page_total).

        Returns:
            Per-page lists of block dicts. Each block has:
            bbox, text, confidence, x, y, w, h
        """
        doc = fitz.open(pdf_path)
        total = len(doc)
        result = []
        for i, page in enumerate(doc):
            img, _ = self._render_page(page)
            blocks = self._detect_text_blocks(img)
            result.append(blocks)
            if progress_callback:
                progress_callback(i + 1, total)
        doc.close()
        logger.info("Detected text blocks across %d pages", len(result))
        return result

    def detect_bboxes_only(self, pdf_path: str, progress_callback=None) -> list[list[dict]]:
        """Detect text block positions only (ignore EasyOCR text quality).

        Uses EasyOCR for bounding box detection, then discards the recognized
        text — use this when you plan to supply text from a better OCR engine.

        Args:
            pdf_path: Path to the scanned PDF.
            progress_callback: Optional callable(page_done, page_total).

        Returns:
            Per-page lists of block dicts with bbox, x, y, w, h (text="").
        """
        doc = fitz.open(pdf_path)
        total = len(doc)
        result = []
        for i, page in enumerate(doc):
            img, _ = self._render_page(page)
            blocks = self._detect_text_blocks(img)
            # Keep bboxes but clear text — Vision API will supply accurate text
            for b in blocks:
                b["_easyocr_text"] = b["text"]  # keep for debugging/fallback
                b["text"] = ""
            result.append(blocks)
            if progress_callback:
                progress_callback(i + 1, total)
        doc.close()
        logger.info("Detected bboxes across %d pages (text cleared)", len(result))
        return result

    @staticmethod
    def _split_ocr_lines(ocr_text: str) -> list[str]:
        """Split Vision API OCR output into lines, filtering empty/whitespace.

        The OCR prompt asks for line-by-line output in reading order, so each
        non-empty line maps to one text block detected by EasyOCR.
        """
        return [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]

    def ocr_pages_hybrid(
        self,
        pdf_path: str,
        ocr_api,  # PdfOcr instance
        progress_callback=None,
    ) -> list[list[dict]]:
        """Hybrid OCR: EasyOCR bboxes + Vision API text = clean blocks.

        Pipeline per page:
        1. EasyOCR detects bbox positions (__detect_text_blocks__)
        2. Vision API (Qwen3-VL) OCRs the full page → clean text lines
        3. Zip lines → bboxes by reading order (both are top→bottom, left→right)

        Args:
            pdf_path: Path to the scanned PDF.
            ocr_api: A PdfOcr instance ready for OCR calls.
            progress_callback: Optional callable(page_done, page_total).

        Returns:
            Per-page lists of block dicts with accurate Vision API text.
        """
        doc = fitz.open(pdf_path)
        total = len(doc)
        result = []

        for pi in range(total):
            page = doc[pi]

            # 1. Render page image
            img_bgr, scale = self._render_page(page)

            # 2. Surya line detection (paragraph-level, clean bboxes)
            bboxes = self._detect_text_blocks_surya(img_bgr)
            if not bboxes:
                result.append([])
                if progress_callback:
                    progress_callback(pi + 1, total)
                continue

            # 3. Vision API OCR on full page
            from handlers.pdf_ocr import PdfOcrError
            try:
                img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                from PIL import Image
                import io as _io
                pil_img = Image.fromarray(img_rgb)
                buf = _io.BytesIO()
                pil_img.save(buf, format="PNG")
                ocr_text = ocr_api.ocr_page(buf.getvalue())
            except PdfOcrError:
                logger.warning("Page %d: Vision OCR failed, using EasyOCR fallback", pi + 1)
                # Fallback: restore EasyOCR text
                for b in bboxes:
                    b["text"] = b.get("_easyocr_text", b.get("text", ""))
                result.append(bboxes)
                if progress_callback:
                    progress_callback(pi + 1, total)
                continue

            # 4. Split Vision text into lines, match to bboxes by position order
            ocr_lines = self._split_ocr_lines(ocr_text)

            for bi, blk in enumerate(bboxes):
                if bi < len(ocr_lines):
                    blk["text"] = ocr_lines[bi]
                else:
                    # Fallback: more bboxes than OCR lines
                    blk["text"] = blk.get("_easyocr_text", "")

            # If OCR returned more lines than bboxes, discard extras
            # (they're usually decorative fragments without matching bbox)

            result.append(bboxes)
            if progress_callback:
                progress_callback(pi + 1, total)

        doc.close()
        logger.info("Hybrid OCR complete: %d pages", total)
        return result

    @staticmethod
    def _merge_bboxes_into_lines(blocks: list[dict], y_tolerance: float = 0.6,
                                  x_gap_max: int = 100) -> list[dict]:
        """Merge fragmented EasyOCR bboxes into line-level blocks.

        EasyOCR often splits a single text line into many tiny bboxes (word or
        even character fragments). This merges them back into clean line blocks,
        producing larger, fewer bboxes that match Vision API text lines better
        and give cleaner translation overlays.

        Args:
            blocks: EasyOCR-detected blocks with bbox, x, y, w, h.
            y_tolerance: Fraction of block height allowed for y-deviation
                         within the same line (0.6 = 60% of height).
            x_gap_max: Max horizontal gap (pixels) between blocks on the
                       same line before they're considered separate.

        Returns:
            Merged line-level blocks with recalculated bbox, x, y, w, h.
        """
        if not blocks:
            return []

        # Sort by y (top to bottom), then x (left to right)
        sorted_blocks = sorted(blocks, key=lambda b: (b["y"], b["x"]))

        lines = []
        current_line = [sorted_blocks[0]]
        avg_h = sorted_blocks[0]["h"]

        for blk in sorted_blocks[1:]:
            last = current_line[-1]
            # Check if on same line: y overlap within tolerance
            y_overlap_start = max(last["y"], blk["y"])
            y_overlap_end = min(last["y"] + last["h"], blk["y"] + blk["h"])
            y_overlap = y_overlap_end - y_overlap_start
            min_h = min(last["h"], blk["h"])
            same_line = y_overlap > min_h * y_tolerance
            close_enough = (blk["x"] - (last["x"] + last["w"])) < x_gap_max

            if same_line and close_enough:
                current_line.append(blk)
                avg_h = max(avg_h, blk["h"])
            else:
                # Finish current line
                lines.append(current_line)
                current_line = [blk]
                avg_h = blk["h"]

        lines.append(current_line)  # don't forget the last line

        # Merge each line group into a single block
        merged = []
        for line_blocks in lines:
            if not line_blocks:
                continue
            # Merge bbox: union of all block rects
            min_x = min(b["x"] for b in line_blocks)
            min_y = min(b["y"] for b in line_blocks)
            max_x = max(b["x"] + b["w"] for b in line_blocks)
            max_y = max(b["y"] + b["h"] for b in line_blocks)
            merged_w = max_x - min_x
            merged_h = max_y - min_y

            # Combine text (use best EasyOCR text as fallback)
            texts = [b.get("_easyocr_text", b.get("text", "")) for b in line_blocks]
            combined_text = " ".join(t for t in texts if t)

            # Create merged bbox polygon
            merged_bbox = [
                [min_x, min_y], [max_x, min_y],
                [max_x, max_y], [min_x, max_y],
            ]

            merged.append({
                "bbox": merged_bbox,
                "text": combined_text,
                "_easyocr_text": combined_text,
                "confidence": max(b.get("confidence", 0) for b in line_blocks),
                "x": min_x,
                "y": min_y,
                "w": merged_w,
                "h": merged_h,
            })

        return merged

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
        blocks_per_page: list[list[dict]] = None,
    ) -> str:
        """Rebuild a scanned PDF with translated text.

        Pipeline: render page → inpaint erased text → insert image as
        background → overlay real PDF text (selectable, searchable).

        Args:
            input_pdf: Path to the scanned PDF file.
            translations: Per-page lists of translated text blocks.
            bilingual: If True, original text kept; translation placed below.
            progress_callback: Optional callable(page_idx, total_pages).
            blocks_per_page: Optional pre-detected blocks.

        Returns:
            Path to the output PDF file.
        """
        doc = fitz.open(input_pdf)
        total = len(doc)
        out_doc = fitz.open()

        # Find CJK font file for real text overlay
        cjk_font_path = self._find_cjk_font()

        for pi in range(total):
            page = doc[pi]
            page_rect = page.rect

            # 1. Render page to image at RENDER_DPI
            img, scale = self._render_page(page)

            # 2. Use pre-detected blocks or re-detect
            if blocks_per_page and pi < len(blocks_per_page):
                blocks = blocks_per_page[pi]
            else:
                blocks = self._detect_text_blocks(img)

            # 3. Erase original text from image
            if not bilingual:
                img = self._erase_text(img, blocks)

            # 4. Insert cleaned image as page background
            out_page = out_doc.new_page(width=page_rect.width, height=page_rect.height)
            img_bytes = self._pil_to_png(img)
            out_page.insert_image(page_rect, stream=img_bytes)

            # 5. Overlay real PDF text (selectable, searchable)
            page_trans = translations[pi] if pi < len(translations) else []
            self._overlay_pdf_text(
                out_page, blocks, page_trans, bilingual, scale, cjk_font_path)

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

    @staticmethod
    def _group_into_paragraphs(blocks: list[dict]) -> list[list[dict]]:
        """Group line-level blocks into paragraphs by y-gap analysis.

        Two consecutive lines belong to the same paragraph if:
        - Their x-start positions are similar (same indent level)
        - The vertical gap between them is small (≤ 1.8× median line height)

        Returns list of paragraphs, each being a list of block dicts.
        """
        if not blocks:
            return []

        # Sort by y first
        sorted_blocks = sorted(blocks, key=lambda b: b["y"])

        # Calculate median line height for gap threshold
        heights = [b["h"] for b in sorted_blocks if b["h"] > 0]
        median_h = sorted(heights)[len(heights) // 2] if heights else 20
        gap_threshold = median_h * 1.8

        paragraphs = []
        current_para = [sorted_blocks[0]]

        for blk in sorted_blocks[1:]:
            prev = current_para[-1]
            # Vertical gap from bottom of prev to top of current
            v_gap = blk["y"] - (prev["y"] + prev["h"])
            # Horizontal position similarity
            x_diff = abs(blk["x"] - prev["x"])

            same_para = v_gap < gap_threshold and x_diff < 50

            if same_para:
                current_para.append(blk)
            else:
                paragraphs.append(current_para)
                current_para = [blk]

        paragraphs.append(current_para)
        return paragraphs

    def rebuild_clean_pages(
        self,
        input_pdf: str,
        translations: list[list[str]],
        bilingual: bool = False,
        progress_callback=None,
        blocks_per_page: list[list[dict]] = None,
    ) -> str:
        """Create clean translated pages with consistent typography.

        For each original page, creates a new page with the translated text
        laid out in paragraphs using uniform font/size/spacing.
        Ignores original images — pure text layout.

        Args:
            input_pdf: Original PDF path (used for page dimensions).
            translations: Per-page translated text (1:1 with blocks_per_page).
            bilingual: If True, keeps original pages interleaved with translations.
            progress_callback: Optional callable(page_done, total_pages).
            blocks_per_page: Pre-detected blocks from Surya.

        Returns:
            Path to the output PDF.
        """
        doc = fitz.open(input_pdf)
        total = len(doc)
        out_doc = fitz.open()

        # Typography constants (in PDF points)
        MARGIN = 50
        BODY_SIZE = 11
        HEADING_SCALE = 1.4  # heading font = body * this
        LINE_SPACING = 1.6
        PARA_SPACING = 8  # extra space between paragraphs

        # Load CJK font
        font_path = self._find_cjk_font()
        font = fitz.Font(fontfile=font_path)

        for pi in range(total):
            page = doc[pi]
            page_w = page.rect.width
            page_h = page.rect.height
            usable_w = page_w - 2 * MARGIN

            # Get blocks and translations for this page
            if blocks_per_page and pi < len(blocks_per_page):
                blocks = blocks_per_page[pi]
            else:
                # Render + detect if no pre-detected blocks
                img, scale = self._render_page(page)
                blocks = self._detect_text_blocks(img)

            page_trans = translations[pi] if pi < len(translations) else []

            # Group blocks into paragraphs
            paragraphs = self._group_into_paragraphs(blocks)

            # Map translations to paragraphs
            trans_idx = 0
            para_translations = []
            for para in paragraphs:
                para_texts = []
                for blk in para:
                    if trans_idx < len(page_trans) and page_trans[trans_idx].strip():
                        para_texts.append(page_trans[trans_idx])
                    trans_idx += 1
                if para_texts:
                    para_translations.append("".join(para_texts))

            if not para_translations:
                if progress_callback:
                    progress_callback(pi + 1, total)
                continue

            # Create new page
            out_page = out_doc.new_page(width=page_w, height=page_h)
            tw = fitz.TextWriter(out_page.rect)
            tw.color = (0, 0, 0)

            cursor_y = MARGIN
            first_para = True

            for pi2, para_text in enumerate(para_translations):
                if not para_text.strip():
                    continue

                # Determine if this paragraph is a heading (short, large original)
                para_blocks = paragraphs[pi2]
                avg_h = sum(b["h"] for b in para_blocks) / len(para_blocks)
                is_heading = len(para_text) < 80 and avg_h > BODY_SIZE * 1.5

                fs = BODY_SIZE * HEADING_SCALE if is_heading else BODY_SIZE

                # Paragraph spacing
                if not first_para:
                    cursor_y += PARA_SPACING

                # Fill text into the usable width
                rect = fitz.Rect(MARGIN, cursor_y, MARGIN + usable_w, page_h - MARGIN)

                try:
                    result = tw.fill_textbox(
                        rect=rect,
                        text=para_text,
                        font=font,
                        fontsize=fs,
                        lineheight=LINE_SPACING,
                        align=0,
                    )
                    # Estimate rendered height: count lines × line height
                    est_lines = max(1, len(para_text) * fs / usable_w)
                    rendered_h = est_lines * fs * LINE_SPACING
                    cursor_y += rendered_h

                    # Check for overflow
                    if result and len(result) > 0 and result[0]:
                        # Text overflowed — start new page
                        tw.write_text(out_page)
                        out_page = out_doc.new_page(width=page_w, height=page_h)
                        tw = fitz.TextWriter(out_page.rect)
                        tw.color = (0, 0, 0)
                        cursor_y = MARGIN
                        rect = fitz.Rect(MARGIN, cursor_y, MARGIN + usable_w, page_h - MARGIN)
                        tw.fill_textbox(
                            rect=rect, text=result[0],
                            font=font, fontsize=fs, lineheight=LINE_SPACING, align=0,
                        )
                        cursor_y += fs * LINE_SPACING
                except Exception:
                    cursor_y += fs * LINE_SPACING * 2

                first_para = False

            tw.write_text(out_page)

            # If bilingual, insert original page before the translation
            if bilingual and pi > 0:
                # Move the translation page after the original
                # (original pages are inserted at the beginning then translations)
                pass

            if progress_callback:
                progress_callback(pi + 1, total)

        # If bilingual: interleave original + translation pages
        if bilingual:
            # Rebuild with interleaving
            original = fitz.open(input_pdf)
            interleaved = fitz.open()
            for pi in range(total):
                # Original page
                interleaved.insert_pdf(original, from_page=pi, to_page=pi)
                # Translation page
                if pi < out_doc.page_count:
                    interleaved.insert_pdf(out_doc, from_page=pi, to_page=pi)
            out_doc = interleaved
            original.close()

        # Save
        page_count = out_doc.page_count
        base, _ = os.path.splitext(input_pdf)
        output_path = f"{base}_clean.pdf"
        out_doc.save(output_path)
        out_doc.close()
        doc.close()

        logger.info("Clean pages PDF saved: %s (%d pages)", output_path, page_count)
        return output_path

    @staticmethod
    def _build_clean_text_pdf(
        page_texts: list[str],
        page_sizes: list[tuple[float, float]],
        output_path: str,
        font_path: str = None,
        bilingual: bool = False,
        original_pdf: str = None,
    ) -> str:
        """Build a clean-text PDF from translated page texts.

        Args:
            page_texts: Translated text per original page.
            page_sizes: (width, height) per original page in PDF points.
            output_path: Where to save the output PDF.
            font_path: CJK font file path.
            bilingual: If True, interleave original scanned page before each translation.
            original_pdf: Path to original PDF (required if bilingual=True).

        Returns:
            Path to the saved PDF.
        """
        if font_path is None:
            font_path = PdfScannedRebuilder._find_cjk_font()

        MARGIN = 45
        BODY_SIZE = 9
        LINE_SPACING = 1.5
        PARA_SPACING = 4

        font = fitz.Font(fontfile=font_path)
        trans_doc = fitz.open()  # translation pages

        for pi, text in enumerate(page_texts):
            if not text.strip():
                continue

            pw, ph = page_sizes[pi] if pi < len(page_sizes) else (595, 842)
            usable_w = pw - 2 * MARGIN

            # Split into paragraphs by blank lines
            raw_paras = text.split("\n\n")
            paragraphs = [p.strip().replace("\n", "") for p in raw_paras if p.strip()]

            cursor_y = MARGIN
            out_page = trans_doc.new_page(width=pw, height=ph)
            tw = fitz.TextWriter(out_page.rect)
            tw.color = (0, 0, 0)

            for para in paragraphs:
                est_lines = max(1, len(para) * BODY_SIZE / usable_w)
                est_height = est_lines * BODY_SIZE * LINE_SPACING + PARA_SPACING

                if cursor_y + est_height > ph - MARGIN:
                    tw.write_text(out_page)
                    out_page = trans_doc.new_page(width=pw, height=ph)
                    tw = fitz.TextWriter(out_page.rect)
                    tw.color = (0, 0, 0)
                    cursor_y = MARGIN

                rect = fitz.Rect(MARGIN, cursor_y, MARGIN + usable_w, ph - MARGIN)
                tw.fill_textbox(
                    rect=rect, text=para, font=font,
                    fontsize=BODY_SIZE, lineheight=LINE_SPACING, align=0,
                )
                cursor_y += est_height

            tw.write_text(out_page)

        # Interleave original + translation if bilingual
        if bilingual and original_pdf:
            original = fitz.open(original_pdf)
            final_doc = fitz.open()
            for pi in range(len(original)):
                # Original scanned page
                final_doc.insert_pdf(original, from_page=pi, to_page=pi)
                # Translation page
                if pi < trans_doc.page_count:
                    final_doc.insert_pdf(trans_doc, from_page=pi, to_page=pi)
            trans_doc.close()
            original.close()
            trans_doc = final_doc

        try:
            trans_doc.save(output_path)
        except Exception:
            # File in use — use alternative name
            import time
            alt = output_path.replace(".pdf", f"_{int(time.time())}.pdf")
            trans_doc.save(alt)
            output_path = alt
        trans_doc.close()
        return output_path

    @staticmethod
    def _overlay_pdf_text(
        page,  # fitz.Page
        blocks: list[dict],
        translations: list[str],
        bilingual: bool,
        scale: float,
        font_path: str,
    ):
        """Overlay real PDF text using TextWriter (embeds font correctly).

        Converts pixel coordinates → PDF points. Uses fill_textbox for
        auto-wrapping within bbox rectangles.
        """
        # Load CJK font (embeds into PDF)
        font = fitz.Font(fontfile=font_path)
        tw = fitz.TextWriter(page.rect)
        tw.color = (0, 0, 0)  # black text

        for i, (blk, trans) in enumerate(zip(blocks, translations)):
            if i >= len(translations):
                break
            if not trans.strip():
                continue

            x, y, w, h = blk["x"], blk["y"], blk["w"], blk["h"]

            # Convert pixels → PDF points
            x_pt = x / scale
            y_pt = y / scale
            w_pt = w / scale
            h_pt = h / scale

            # Font size: proportional to original bbox height
            font_size = max(6, h_pt * 0.8)

            if bilingual:
                pos_y = y_pt + h_pt
            else:
                pos_y = y_pt

            try:
                tw.fill_textbox(
                    rect=fitz.Rect(x_pt, pos_y, x_pt + w_pt, pos_y + h_pt * 2),
                    text=trans,
                    font=font,
                    fontsize=font_size,
                    align=0,
                )
            except Exception:
                pass  # Skip if text doesn't fit

        tw.write_text(page)

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

        Uses height-based font sizing: font_size = bbox_height * 0.75,
        ensuring uniform text size across all blocks on the same page.
        Text is wrapped to fit within the bbox width.
        """
        if not translations:
            return img

        # Convert to PIL for text drawing
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)

        # Calculate uniform font size from median bbox height
        heights = [blk["h"] for blk in blocks[:len(translations)] if blk["h"] > 0]
        if heights:
            median_h = sorted(heights)[len(heights) // 2]
        else:
            median_h = 20
        font_size = max(8, int(median_h * 0.75))

        for i, (blk, trans) in enumerate(zip(blocks, translations)):
            if i >= len(translations):
                break
            if not trans:
                continue

            x, y, w, h = blk["x"], blk["y"], blk["w"], blk["h"]

            if bilingual:
                trans_y = y + h + 4
            else:
                trans_y = y

            # Wrap text to fit bbox width
            lines = self._wrap_text_pil(trans, font_size, w)

            # Draw each line
            line_h = int(font_size * 1.4)
            max_lines = int(h / line_h) if not bilingual else int(h * 1.5 / line_h)
            for li, line in enumerate(lines):
                if li >= max_lines:
                    break
                ly = trans_y + li * line_h
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
        """Find a CJK-capable serif font on the system (SourceHanSerif / NotoSerifSC preferred)."""
        # Windows: prefer STSONG (华文宋体) then NotoSerifSC
        candidates = [
            "C:/Windows/Fonts/STSONG.TTF",
            "C:/Windows/Fonts/NotoSerifSC-VF.ttf",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/SimsunExtG.ttf",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        # Linux / Mac
        linux_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
        for path in linux_candidates:
            if os.path.exists(path):
                return path
        raise FileNotFoundError(
            "No CJK font found. Install Noto Serif CJK or SourceHanSerifCN."
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
