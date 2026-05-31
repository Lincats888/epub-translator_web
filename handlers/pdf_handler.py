"""PDF handler — translate text-based PDF documents using PyMuPDF.

Only handles text-based (non-scanned) PDFs. Scanned PDFs are detected
and rejected with a clear error message.

Extracts text blocks grouped into paragraphs by vertical spacing.
Rebuild preserves font, size, color, and position.
"""

import os
import re
import shutil

from .base import BaseHandler, TextFragment

_fitz = None


def _get_fitz():
    global _fitz
    if _fitz is None:
        import fitz as _f
        _fitz = _f
    return _fitz


# ── Code detection ──────────────────────────────────────────────────
_CODE_PATTERNS = (
    re.compile(r'^\s*[{}\[\];]'),
    re.compile(r'^\s*(import |from |def |class |if |for |while )'),
    re.compile(r'^\s*(public |private |void |int |string |procedure |function )'),
    re.compile(r'^\s*(#include|using namespace)'),
    re.compile(r'^\s*<[^>]+>'),
    re.compile(r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE)\s', re.I),
    re.compile(r'^\s*(\{\$|program |uses |begin\b|end\.\b|const |var |type )', re.I),  # Pascal/Delphi
    re.compile(r'^\s*(<\?php |<\?xml |<!doctype )', re.I),
)


def _is_code_like(text):
    if not text or len(text.strip()) < 3:
        return False
    lines = text.strip().split('\n')
    code_lines = sum(1 for l in lines if any(p.search(l) for p in _CODE_PATTERNS))
    if len(lines) >= 1:
        # Single line: must match a pattern
        if code_lines >= 1 and len(lines) == 1:
            return True
        # Multi-line: >40% of lines look like code
        if len(lines) > 2 and code_lines / len(lines) > 0.3:
            return True
    return False


def _is_translatable(text):
    stripped = text.strip()
    if not stripped or len(stripped) < 3:
        return False
    if not re.search(r'[a-zA-Z]', stripped):
        return False
    if _is_code_like(stripped):
        return False
    return True


# ── Handler ─────────────────────────────────────────────────────────

class PdfHandler(BaseHandler):
    """Handler for text-based .pdf documents."""

    @staticmethod
    def supported_extensions():
        return [".pdf"]

    def extract(self, file_path, skip_tags=None, bilingual=True):
        """Extract translatable text blocks from a PDF.

        Strategy:
        1. Detect scanned PDF (reject if >70% pages have no text)
        2. Extract text spans per page with position/format info
        3. Group spans into lines, then paragraphs by vertical spacing
        4. Skip headers/footers (top/bottom 10%), page numbers, code
        """
        fitz = _get_fitz()
        doc = fitz.open(file_path)

        # 1. Scanned PDF detection
        total_pages = len(doc)
        text_pages = sum(1 for i in range(total_pages) if doc[i].get_text().strip())
        if total_pages > 0 and text_pages / total_pages < 0.3:
            doc.close()
            raise ValueError(
                "This PDF appears to be scanned (image-based). "
                "Text-based PDF translation is supported, but scanned PDFs "
                "require OCR which is not yet implemented."
            )

        fragments = []

        for page_num in range(total_pages):
            page = doc[page_num]
            page_height = page.rect.height
            page_width = page.rect.width

            # Skip margins: top 6% and bottom 6% (headers/footers)
            header_limit = page_height * 0.06
            footer_limit = page_height * 0.94

            # Process each text block independently (preserves PyMuPDF's
            # block-level grouping which already reflects paragraphs/headings)
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

            for block in blocks:
                if block["type"] != 0:  # skip non-text blocks
                    continue

                bbox = block["bbox"]
                # Skip headers/footers
                if bbox[1] < header_limit or bbox[3] > footer_limit:
                    continue

                # Collect spans from this block
                block_spans = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                        block_spans.append({
                            "text": text,
                            "bbox": span.get("bbox", [0, 0, 0, 0]),
                            "font": span.get("font", ""),
                            "size": span.get("size", 11),
                            "color": span.get("color", 0),
                            "flags": span.get("flags", 0),
                            "page": page_num,
                        })

                if not block_spans:
                    continue

                # Join all spans in the block as one text
                text = " ".join(s["text"] for s in block_spans)

                # Skip page numbers
                if (len(text) <= 4 and re.match(r'^\d+$', text)
                        and abs(bbox[0] + bbox[2] - page_width) < page_width * 0.3):
                    continue

                if not _is_translatable(text):
                    continue
                if len(text) < 5:
                    continue

                fragments.append(TextFragment(
                    text=text,
                    meta={
                        "type": "pdf_text",
                        "page": page_num,
                        "spans": block_spans,
                        "bbox": bbox,
                    },
                ))

        doc.close()
        return fragments

    def rebuild(self, file_path, fragments, translations, bilingual,
                target_lang="zh-CN"):
        """Write translations back to the PDF.

        Bilingual mode: creates a new PDF with original pages followed
        by translated copies (one translated page per original page).
        This avoids layout conflicts between English and Chinese text.
        """
        fitz = _get_fitz()
        output_path = self._output_path(file_path)

        if bilingual:
            return self._rebuild_bilingual(file_path, output_path,
                                           fragments, translations)
        else:
            return self._rebuild_replace(file_path, output_path,
                                         fragments, translations)

    def _rebuild_replace(self, file_path, output_path, fragments, translations):
        """Replace mode: overwrite original text with translation.
        Fits all blocks on each page proportionally, like 'Fit to Page' printing.
        """
        fitz = _get_fitz()
        shutil.copy2(file_path, output_path)
        doc = fitz.open(output_path)

        trans_map = {self._meta_key(f.meta): t for f, t in zip(fragments, translations)}

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_frags = [
                (f, trans_map[self._meta_key(f.meta)])
                for f in fragments
                if f.meta.get("page") == page_num and self._meta_key(f.meta) in trans_map
            ]
            if not page_frags:
                continue

            # Sort by vertical position
            page_frags.sort(key=lambda x: x[0].meta["bbox"][1])

            page_top = page_frags[0][0].meta["bbox"][1]
            page_bottom = page.rect.height - 30

            # Phase 1: Calculate how many lines each block needs at base font size
            avail_width = page.rect.width - page_frags[0][0].meta["bbox"][0] - 50
            block_info = []
            total_lines = 0
            for frag, trans in page_frags:
                orig_size = frag.meta["spans"][0].get("size", 11)
                font = self._font_for_text(
                    frag.meta["spans"][0].get("font", ""), trans)
                is_cjk = font == self._cjk_font_name
                char_w = orig_size if is_cjk else orig_size * 0.55
                max_ch = max(int(avail_width / char_w), 8)
                lines = self._wrap_text(trans, max_ch)
                block_info.append({
                    "frag": frag, "trans": trans, "lines": len(lines),
                    "orig_size": orig_size, "font": font, "is_cjk": is_cjk,
                    "orig_bbox": frag.meta["bbox"], "spans": frag.meta["spans"],
                })
                total_lines += len(lines)

            # Phase 2: Scale font sizes to fit within page
            avail_height = page_bottom - page_top
            line_h_base = sum(b["orig_size"] * 1.35 for b in block_info for _ in range(b["lines"]))
            if line_h_base > 0 and total_lines > 0:
                scale = min(1.0, avail_height / line_h_base)
            else:
                scale = 1.0

            # Phase 3: Write blocks with adjusted positions
            current_y = page_top
            for bi in block_info:
                bbox = list(bi["orig_bbox"])
                bbox[1] = current_y
                font_size = max(bi["orig_size"] * scale, 6)
                if bi["is_cjk"]:
                    font_size *= 0.88

                char_w = font_size if bi["is_cjk"] else font_size * 0.55
                max_ch = max(int(avail_width / char_w), 8)
                lines = self._wrap_text(bi["trans"], max_ch)
                line_h = font_size * 1.35

                # Clear original area
                rect = fitz.Rect(bi["orig_bbox"])
                page.add_redact_annot(rect)
                page.apply_redactions()

                # Write lines
                total_h = font_size * 1.2 + len(lines) * line_h
                for i, line in enumerate(lines):
                    y = current_y + font_size * 1.2 + i * line_h
                    if y > page_bottom:
                        break
                    try:
                        self._insert_line(page, fitz.Point(bbox[0], y), line,
                                        bi["font"], font_size,
                                        bi["spans"][0].get("color", 0))
                    except Exception:
                        pass

                current_y += total_h + font_size * 0.3  # gap between blocks

        tmp_path = output_path + ".tmp"
        doc.save(tmp_path)
        doc.close()
        os.replace(tmp_path, output_path)
        return output_path

    def _rebuild_bilingual(self, file_path, output_path, fragments, translations):
        """Bilingual mode: alternating original page, then translated page.

        Each original page is followed by its Chinese translation on the next page.
        """
        fitz = _get_fitz()

        trans_map = {self._meta_key(f.meta): t for f, t in zip(fragments, translations)}

        doc_orig = fitz.open(file_path)
        total_pages = len(doc_orig)

        # Create translated version (replace mode on a copy)
        doc_orig.save(output_path)
        doc_orig.close()
        doc_trans = fitz.open(output_path)
        for page_num in range(total_pages):
            page = doc_trans[page_num]
            page_frags = [
                (f, trans_map[self._meta_key(f.meta)])
                for f in fragments
                if f.meta.get("page") == page_num
                and self._meta_key(f.meta) in trans_map
            ]
            for frag, trans in page_frags:
                self._replace_text(page, frag.meta["bbox"], frag.meta["spans"], trans)

        tmp_path = output_path + ".trans.tmp"
        doc_trans.save(tmp_path)
        doc_trans.close()

        # Merge: insert all pages at once for font deduplication,
        # then reorder to alternate original/translated
        out = fitz.open()
        orig = fitz.open(file_path)
        trans = fitz.open(tmp_path)

        out.insert_pdf(orig)   # pages 0..N-1
        out.insert_pdf(trans)  # pages N..2N-1

        orig.close()
        trans.close()

        # Reorder: move each translated page right after its original
        # After insert:  [orig0..origN-1, trans0..transN-1]
        # Target:        [orig0, trans0, orig1, trans1, ...]
        # Move trans[i] from position N+i to position 2*i+1
        N = total_pages
        for i in range(N):
            out.move_page(N + i, 2 * i + 1)

        out_tmp = output_path + ".tmp"
        out.save(out_tmp)
        out.close()
        os.replace(out_tmp, output_path)
        os.unlink(tmp_path)
        return output_path

    # ── Grouping helpers ────────────────────────────────────────────

    @staticmethod
    def _group_spans_into_lines(spans, tolerance=5):
        """Group spans on the same vertical line."""
        if not spans:
            return []
        lines = []
        current_line = [spans[0]]
        current_y = spans[0]["bbox"][1]

        for span in spans[1:]:
            if abs(span["bbox"][1] - current_y) <= tolerance:
                current_line.append(span)
            else:
                lines.append(current_line)
                current_line = [span]
                current_y = span["bbox"][1]

        if current_line:
            lines.append(current_line)

        # Sort spans within each line by x position
        for line in lines:
            line.sort(key=lambda s: s["bbox"][0])

        return lines

    @staticmethod
    def _group_lines_into_paragraphs(lines, page_height):
        """Group lines into paragraphs by vertical spacing.

        A new paragraph starts when the gap between lines exceeds
        1.5x the font size of the preceding line.
        """
        if not lines:
            return []

        paragraphs = []
        current_para = []
        prev_bottom = 0
        prev_size = 11

        for line in lines:
            line_top = line[0]["bbox"][1]
            line_size = line[0].get("size", 11)

            if current_para:
                gap = line_top - prev_bottom
                threshold = max(prev_size * 1.5, 8)  # minimum threshold
                if gap > threshold:
                    paragraphs.append(current_para)
                    current_para = []

            current_para.extend(line)
            prev_bottom = max(s["bbox"][3] for s in line)
            prev_size = line_size

        if current_para:
            paragraphs.append(current_para)

        return paragraphs

    @staticmethod
    def _merge_bbox(spans):
        """Merge multiple span bboxes into one."""
        if not spans:
            return [0, 0, 0, 0]
        x0 = min(s["bbox"][0] for s in spans)
        y0 = min(s["bbox"][1] for s in spans)
        x1 = max(s["bbox"][2] for s in spans)
        y1 = max(s["bbox"][3] for s in spans)
        return [x0, y0, x1, y1]

    # ── Write-back helpers ──────────────────────────────────────────

    def _replace_text(self, page, bbox, spans, text):
        """Replace original text with translation. Convenience wrapper."""
        self._replace_text_scaled(page, bbox, spans, text,
                                   page.rect.height - 30)

    def _replace_text_scaled(self, page, bbox, spans, text, page_bottom):
        """Replace text with auto-scaling to fit page bottom.
        Returns the y-coordinate of the last line written (or None)."""
        fitz = _get_fitz()

        orig_size = spans[0].get("size", 11) if spans else 11
        color = spans[0].get("color", 0) if spans else 0
        font = self._font_for_text(spans[0].get("font", "") if spans else "", text)
        is_cjk = font == self._cjk_font_name

        rect = fitz.Rect(bbox)
        page.add_redact_annot(rect)
        page.apply_redactions()

        avail_width = rect.x1 - rect.x0
        if avail_width <= 0:
            avail_width = page.rect.width - rect.x0 - 50

        # Space available from block start to page bottom
        avail_height = page_bottom - rect.y0
        if avail_height <= 0:
            return None

        # Find font size that fits
        base_size = orig_size * 0.88 if is_cjk else orig_size
        font_size = base_size
        min_font = 6

        while font_size >= min_font:
            char_width = font_size if is_cjk else font_size * 0.55
            max_chars = max(int(avail_width / char_width), 8)
            lines = self._wrap_text(text, max_chars)
            line_height = font_size * 1.35
            total_height = font_size * 1.2 + len(lines) * line_height
            if rect.y0 + total_height <= page_bottom:
                break
            font_size -= 1.5

        if font_size < min_font:
            font_size = min_font

        # Write lines
        char_width = font_size if is_cjk else font_size * 0.55
        max_chars = max(int(avail_width / char_width), 8)
        lines = self._wrap_text(text, max_chars)
        line_height = font_size * 1.35
        last_y = rect.y0

        for i, line in enumerate(lines):
            y = rect.y0 + font_size * 1.2 + i * line_height
            if y > page_bottom:
                break
            try:
                self._insert_line(page, fitz.Point(rect.x0, y), line,
                                  font, font_size, color)
                last_y = y
            except Exception:
                pass

        return last_y if last_y > rect.y0 else None

    def _insert_bilingual(self, page, bbox, spans, trans):
        """Insert translation below original text (bilingual mode)."""
        fitz = _get_fitz()

        font_size = spans[0].get("size", 11) if spans else 11
        color = spans[0].get("color", 0) if spans else 0
        font = self._font_for_text(spans[0].get("font", "") if spans else "", trans)

        y_offset = bbox[3] + font_size * 0.5
        trans_size = font_size * 0.85

        max_chars = max(int((bbox[2] - bbox[0]) / (trans_size * 0.5)), 10)
        lines = self._wrap_text(trans, max_chars)

        for i, line in enumerate(lines):
            y = y_offset + i * (trans_size * 1.3)
            try:
                self._insert_line(page, fitz.Point(bbox[0], y), line,
                                  font, trans_size, color)
            except Exception:
                pass

    @classmethod
    def _insert_line(cls, page, point, text, font, font_size, color):
        """Insert a line of text, handling both built-in and CJK fonts."""
        color_rgb = cls._int_to_rgb(color)
        if font == cls._cjk_font_name:
            cls._ensure_cjk_font_embedded(page)
        page.insert_text(point, text, fontname=font,
                         fontsize=font_size, color=color_rgb)

    # ── Utility ─────────────────────────────────────────────────────

    @staticmethod
    def _meta_key(meta):
        return f"pdf_{meta['page']}_{meta['bbox'][0]:.0f}_{meta['bbox'][1]:.0f}"

    @staticmethod
    def _output_path(file_path):
        base, ext = os.path.splitext(file_path)
        return f"{base}_translated{ext}"

    @staticmethod
    def _int_to_rgb(color_int):
        """Convert PyMuPDF color int to (r, g, b) tuple (0-1 range)."""
        if isinstance(color_int, (tuple, list)):
            return color_int
        r = ((color_int >> 16) & 0xFF) / 255.0
        g = ((color_int >> 8) & 0xFF) / 255.0
        b = (color_int & 0xFF) / 255.0
        return (r, g, b)

    # CJK font cache — embedded per-page (PDF requires this)
    # File size is controlled by insert_pdf() bulk merge which deduplicates fonts
    _cjk_font_name = "F0"
    _cjk_font_data = None

    @classmethod
    def _get_cjk_font_data(cls):
        if cls._cjk_font_data:
            return cls._cjk_font_data
        candidates = [
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        cls._cjk_font_data = f.read()
                    return cls._cjk_font_data
                except Exception:
                    continue
        return None

    @classmethod
    def _ensure_cjk_font_embedded(cls, page):
        """Embed CJK font into the page. Required on every page that
        uses CJK text (PDF fonts are per-page resources)."""
        data = cls._get_cjk_font_data()
        if data is None:
            return
        try:
            page.insert_font(fontname=cls._cjk_font_name, fontbuffer=data)
        except Exception:
            pass

    @classmethod
    def _font_for_text(cls, pdf_font_name, text):
        """Get font name for writing text. Returns 'F0' for CJK."""
        has_cjk = any('一' <= c <= '鿿' or '぀' <= c <= 'ヿ' or
                       '가' <= c <= '힯' for c in text)
        if has_cjk and cls._get_cjk_font_data():
            return cls._cjk_font_name
        return cls._map_font(pdf_font_name)

    @staticmethod
    def _map_font(pdf_font_name):
        """Map PDF font name to PyMuPDF built-in font or CJK font path."""
        """Map PDF font name to PyMuPDF built-in font."""
        if not pdf_font_name:
            return "helv"
        lower = pdf_font_name.lower()
        if "bold" in lower and "italic" in lower:
            return "helv"  # PyMuPDF doesn't have bold-italic built-in
        if "bold" in lower:
            return "helv"
        if "italic" in lower or "oblique" in lower:
            return "helv"
        if "mono" in lower or "courier" in lower or "consolas" in lower:
            return "cobo"
        if "serif" in lower or "times" in lower or "georgia" in lower:
            return "serif"
        return "helv"

    @staticmethod
    def _wrap_text(text, max_chars):
        """Wrap text to fit within max_chars per line.

        Works for both Latin (word-wrap) and CJK (character-wrap) text
        mixed in any proportion. Characters longer than max_chars are
        force-split.
        """
        if not text or max_chars <= 0:
            return [text]
        if len(text) <= max_chars:
            return [text]

        words = text.split()
        lines = []
        current = ""

        for word in words:
            # If the word itself is too long, char-wrap it
            while len(word) > max_chars:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]

            # Try to add word to current line
            test = f"{current} {word}".strip() if current else word
            if len(test) <= max_chars:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word

        if current:
            lines.append(current)

        return lines if lines else [text[:max_chars]]
