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
    re.compile(r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|select|insert|update|delete|create)\s'),  # SQL
    re.compile(r'^\s*(uses )'),  # Pascal/Delphi (case-sensitive — "Uses" at sentence start is English)
    re.compile(r'^\s*(\{\$|program |begin\b|end\.\b|const |var |type )'),  # Pascal/Delphi (lowercase only, to avoid "Program"/"Begin"/"Type" etc. as English)
    re.compile(r'^\s*(<\?php|<\?xml|<!doctype)'),  # markup/script declarations
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

            # Skip margins: top 6% and bottom 6% (headers/footers).
            # Use both position AND font size: large text near edges is
            # likely a heading or content, not a page number/footer.
            header_limit = page_height * 0.06
            footer_limit = page_height * 0.94

            # Process each text block independently (preserves PyMuPDF's
            # block-level grouping which already reflects paragraphs/headings)
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

            page_frags = []

            for block in blocks:
                if block["type"] != 0:  # skip non-text blocks
                    continue

                bbox = block["bbox"]
                # Skip headers/footers — but only if the text is small
                # (page numbers, running headers). Content-sized text
                # (>= 9pt) near edges is likely a heading or section title.
                first_span_size = None
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            first_span_size = span.get("size", 11)
                            break
                    if first_span_size is not None:
                        break
                is_marginal = (bbox[1] < header_limit or bbox[3] > footer_limit)
                if is_marginal and first_span_size is not None and first_span_size < 9:
                    continue

                # Collect spans from this block
                block_spans = []
                block_dir = None  # rotation direction of the block
                for line in block.get("lines", []):
                    line_dir = line.get("dir", (1.0, 0.0))
                    for span in line.get("spans", []):
                        span_text = span.get("text", "").strip()
                        if not span_text:
                            continue
                        if block_dir is None:
                            block_dir = line_dir
                        block_spans.append({
                            "text": span_text,
                            "bbox": span.get("bbox", [0, 0, 0, 0]),
                            "font": span.get("font", ""),
                            "size": span.get("size", 11),
                            "color": span.get("color", 0),
                            "flags": span.get("flags", 0),
                            "page": page_num,
                            "dir": line_dir,
                            "origin": span.get("origin", [0, 0]),
                        })

                if not block_spans:
                    continue

                # ── Split block into colour/x-position groups ──
                # When a single PyMuPDF block contains multiple spans with
                # different colours separated by wide gaps (e.g. icon labels
                # "LEARNING  PROJECTS  JOB"), split them into individual
                # fragments so each retains its own colour and position.
                def _should_split(spans):
                    if len(spans) <= 1:
                        return False
                    sorted_s = sorted(spans, key=lambda s: s["bbox"][0])
                    # Split if spans have wide x-gaps (> 80 px) — this
                    # handles header-left + "CHAPTER 3" at opposite sides.
                    for a, b in zip(sorted_s, sorted_s[1:]):
                        if b["bbox"][0] - a["bbox"][2] > 80:
                            return True
                    # Also split if spans have different colours
                    colors = {s.get("color", 0) for s in spans}
                    return len(colors) > 1

                # Group spans by color (adjacent same-color spans stay together)
                def _color_groups(spans):
                    sorted_s = sorted(spans, key=lambda s: s["bbox"][0])
                    groups = [[sorted_s[0]]]
                    for s in sorted_s[1:]:
                        prev = groups[-1][-1]
                        gap = s["bbox"][0] - prev["bbox"][2]
                        # New group on wide gap OR colour change
                        if (gap > 80 or s.get("color", 0) != prev.get("color", 0)):
                            groups.append([s])
                        else:
                            groups[-1].append(s)
                    return groups

                sub_blocks = []
                if _should_split(block_spans):
                    for grp in _color_groups(block_spans):
                        sub_blocks.append(grp)
                else:
                    sub_blocks = [block_spans]

                for sb_spans in sub_blocks:
                    text = " ".join(s["text"] for s in sb_spans)
                    sb_bbox = self._merge_bbox(sb_spans)

                    # Skip page numbers
                    if (len(text) <= 4 and re.match(r'^\d+$', text)
                            and abs(sb_bbox[0] + sb_bbox[2] - page_width) < page_width * 0.3):
                        continue

                    if not _is_translatable(text):
                        continue
                    min_len = 2 if text.isupper() else 5
                    if len(text) < min_len:
                        continue

                    page_frags.append(TextFragment(
                        text=text,
                        meta={
                            "type": "pdf_text",
                            "page": page_num,
                            "spans": sb_spans,
                            "bbox": sb_bbox,
                            "dir": block_dir or (1.0, 0.0),
                        },
                    ))

            # ── Merge consecutive fragments split across visual lines ──
            # Heuristic: merge when vertical gap is smaller than expected
            # paragraph spacing (gap < 2×font_size) and the previous fragment
            # is clearly an incomplete sentence (no terminal punctuation).
            #
            # Groups fragments by text column first, then merges within each
            # column. This handles multi-column layouts (e.g. TOC + sidebar
            # labels) where a sidebar item sits between two fragments that
            # should merge.
            if len(page_frags) > 1:
                # Group into columns by x0 (left edge) rounded to 20px bins.
                # This keeps short and long lines in the same column together.
                from collections import defaultdict
                col_bins = defaultdict(list)
                for f in page_frags:
                    x0 = f.meta["bbox"][0]
                    col_bins[round(x0 / 20)].append(f)

                merged_per_col = []
                for col_key in sorted(col_bins):
                    col_frags = sorted(col_bins[col_key],
                                       key=lambda x: x.meta["bbox"][1])
                    current = col_frags[0]
                    for next_frag in col_frags[1:]:
                        cur_bbox = current.meta["bbox"]
                        nxt_bbox = next_frag.meta["bbox"]
                        cur_size = current.meta["spans"][0].get("size", 11)
                        nxt_size = next_frag.meta["spans"][0].get("size", 11)

                        gap = nxt_bbox[1] - cur_bbox[3]
                        if gap > 0 and gap < max(cur_size, nxt_size) * 2.0:
                            size_ratio = max(cur_size, nxt_size) / max(min(cur_size, nxt_size), 1)
                            no_terminal = current.text[-1] not in ".!?:"
                        else:
                            size_ratio = 99
                            no_terminal = False

                        if (gap > 0 and gap < max(cur_size, nxt_size) * 2.0
                                and size_ratio < 1.3 and no_terminal):
                            current.text = current.text + " " + next_frag.text
                            current.meta["bbox"] = (
                                min(cur_bbox[0], nxt_bbox[0]),
                                min(cur_bbox[1], nxt_bbox[1]),
                                max(cur_bbox[2], nxt_bbox[2]),
                                max(cur_bbox[3], nxt_bbox[3]),
                            )
                            current.meta["spans"].extend(next_frag.meta["spans"])
                        else:
                            merged_per_col.append(current)
                            current = next_frag
                    merged_per_col.append(current)

                page_frags = sorted(merged_per_col,
                                    key=lambda f: f.meta["bbox"][1])

            fragments.extend(page_frags)

        doc.close()
        return fragments

    # ── pdf2zh integration ────────────────────────────────────────────

    @staticmethod
    def _find_pdf2zh():
        """Find the pdf2zh binary. Cross-platform."""
        import shutil
        # 1. Check PATH (pip install)
        for name in ("pdf2zh", "pdf2zh.exe"):
            found = shutil.which(name)
            if found:
                return found
        # 2. Check uv install location
        home = os.path.expanduser("~")
        for sub in (".local/bin/pdf2zh", ".local/bin/pdf2zh.exe",
                     ".cargo/bin/pdf2zh", ".cargo/bin/pdf2zh.exe"):
            path = os.path.join(home, sub)
            if os.path.exists(path):
                return path
        return None

    @classmethod
    def rebuild_via_pdf2zh(cls, file_path, output_dir=None, service="deepseek"):
        """Use PDFMathTranslate to translate and rebuild the PDF.

        This bypasses our custom extract/translate/rebuild pipeline and
        delegates to pdf2zh's content-stream-level reconstruction, which
        preserves layout significantly better than our bbox-based approach.
        """
        import subprocess
        import shutil

        exe = cls._find_pdf2zh()
        if not exe:
            raise FileNotFoundError(
                "pdf2zh not found. "
                "Install with: pip install uv && uv tool install --python 3.12 pdf2zh"
            )

        cmd = [exe, file_path, "-s", service]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # pdf2zh writes OUTPUT-dual.pdf and OUTPUT-mono.pdf alongside input
        base = os.path.splitext(file_path)[0]
        dual = f"{base}-dual.pdf"

        if not os.path.exists(dual):
            raise RuntimeError(
                f"pdf2zh failed:\n{result.stdout}\n{result.stderr}"
            )

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            dest = os.path.join(output_dir,
                                os.path.basename(base) + "_dual.pdf")
            shutil.move(dual, dest)
            return dest
        return dual

    @staticmethod
    def is_pdf2zh_available():
        return PdfHandler._find_pdf2zh() is not None

    def rebuild(self, file_path, fragments, translations, bilingual,
                target_lang="zh-CN", method="native"):
        """Write translations back to the PDF.

        method='native': use our PyMuPDF bbox-based rebuild (exact bbox,
            font shrinks to fit).
        method='pdf2zh': delegate to PDFMathTranslate for content-stream-
            level reconstruction (better layout, separate pipeline).

        Bilingual mode: creates a new PDF with original pages followed
        by translated copies (one translated page per original page).
        """
        if method == "pdf2zh":
            return self.rebuild_via_pdf2zh(file_path)

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

        Uses per-block optimal font sizing + TextWriter.fill_textbox()
        for pixel-perfect layout within each block's original bounding box.
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

            page_bottom = page.rect.height - 30

            # Compute text column boundaries from all fragments on this page.
            # This gives centered/indented headings access to the full column
            # width, preventing Chinese text from being cramped into a narrow
            # English bbox and causing premature line-wrapping + font shrinkage.
            col_left, col_right = self._page_column_bounds(
                [f for f, _ in page_frags])

            for i, (frag, trans) in enumerate(page_frags):
                bbox = frag.meta["bbox"]
                spans = frag.meta["spans"]
                orig_size = spans[0].get("size", 11)
                color = spans[0].get("color", 0)

                # Build font object
                font_obj, _ = self._build_font_obj(trans,
                                                   spans[0].get("font", ""))

                # ── Available width ──
                # Use page column width starting at block's left edge.
                # English single-line headings often have a tight centered
                # bbox; using column width avoids forcing Chinese to wrap
                # prematurely.
                avail_w = max(col_right - bbox[0], bbox[2] - bbox[0])
                if avail_w <= 0:
                    continue

                # ── Available height ──
                # From block top to next block top (or page bottom).
                # This lets Chinese text flow downward into paragraph
                # spacing rather than being crammed into the original bbox.
                if i < len(page_frags) - 1:
                    avail_h = page_frags[i + 1][0].meta["bbox"][1] - bbox[1]
                else:
                    avail_h = page_bottom - bbox[1]

                if avail_h <= 0:
                    avail_h = bbox[3] - bbox[1]
                    if avail_h <= 0:
                        continue

                # ── Binary-search optimal font size for this block ──
                opt_size = self._optimal_font_size(trans, font_obj,
                                                   avail_w, avail_h,
                                                   orig_size)

                # ── Redact original area (keep it tight to original bbox) ──
                page.add_redact_annot(fitz.Rect(bbox))
                try:
                    page.apply_redactions()
                except Exception:
                    pass

                # ── Write with column-width rect matching calc space ──
                # Redact=False because we already cleared the original bbox above.
                write_rect = fitz.Rect(bbox[0], bbox[1],
                                       col_right, bbox[1] + avail_h)
                self._write_block_with_textwriter(page, write_rect, trans,
                                                  font_obj, opt_size, color,
                                                  redact=False)

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
            if not page_frags:
                continue

            # Sort by vertical position (top-to-bottom).
            page_frags.sort(key=lambda x: x[0].meta["bbox"][1])

            for i, (frag, trans) in enumerate(page_frags):
                bbox = frag.meta["bbox"]
                spans = frag.meta["spans"]
                orig_size = spans[0].get("size", 11)
                color = spans[0].get("color", 0)
                font_obj, _ = self._build_font_obj(
                    trans, spans[0].get("font", ""))

                avail_w = max(bbox[2] - bbox[0], 20)
                avail_h = max(bbox[3] - bbox[1], 20)

                opt_size = self._optimal_font_size(
                    trans, font_obj, avail_w, avail_h, orig_size)

                page.add_redact_annot(fitz.Rect(bbox))
                try:
                    page.apply_redactions()
                except Exception:
                    pass

                dir_vec = frag.meta.get("dir", (1.0, 0.0))
                is_rotated = (abs(dir_vec[0] - 1.0) > 0.001
                              or abs(dir_vec[1]) > 0.001)

                if is_rotated:
                    self._write_rotated_block(
                        page, frag, trans, font_obj, opt_size, color)
                else:
                    self._write_block_with_textwriter(
                        page, fitz.Rect(bbox), trans, font_obj, opt_size,
                        color, redact=False)

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
        """Replace text using optimal font sizing + TextWriter within block's own bbox.
        Returns the y-coordinate of the last line written (or None)."""
        fitz = _get_fitz()

        orig_size = spans[0].get("size", 11) if spans else 11
        color = spans[0].get("color", 0) if spans else 0

        font_obj, _ = self._build_font_obj(
            text, spans[0].get("font", "") if spans else "")

        rect = fitz.Rect(bbox)
        avail_w = rect.x1 - rect.x0
        avail_h = rect.y1 - rect.y0

        if avail_w <= 0 or avail_h <= 0:
            return None

        opt_size = self._optimal_font_size(text, font_obj,
                                           avail_w, avail_h, orig_size)

        self._write_block_with_textwriter(page, rect, text,
                                          font_obj, opt_size, color)

        # Approximate last line y for return value
        approx_last_y = rect.y0 + opt_size * 1.2 + opt_size * 1.35
        return approx_last_y if approx_last_y > rect.y0 else None

    def _insert_bilingual(self, page, bbox, spans, trans):
        """Insert translation below original text (bilingual mode)."""
        fitz = _get_fitz()

        orig_size = spans[0].get("size", 11) if spans else 11
        color = spans[0].get("color", 0) if spans else 0
        font_obj, _ = self._build_font_obj(
            trans, spans[0].get("font", "") if spans else "")

        trans_size = orig_size * 0.85
        y_offset = bbox[3] + orig_size * 0.5

        # Bilingual insertion area: below original bbox, half a page height target
        avail_w = bbox[2] - bbox[0]
        avail_h = (bbox[3] - bbox[1]) * 2  # allow up to 2x orig height below

        if avail_w <= 0:
            return

        opt_size = min(trans_size,
                       self._optimal_font_size(trans, font_obj,
                                               avail_w, avail_h, trans_size))

        rect = fitz.Rect(bbox[0], y_offset, bbox[2], y_offset + avail_h)
        self._write_block_with_textwriter(page, rect, trans,
                                          font_obj, opt_size, color)

    @classmethod
    def _build_font_obj(cls, text, pdf_font_name):
        """Build and cache a fitz.Font object for the given text and PDF font name.

        Returns (fitz.Font, is_cjk) tuple.
        """
        import fitz as _f
        has_cjk = any('一' <= c <= '鿿' or '぀' <= c <= 'ヿ' or
                      '가' <= c <= '힯' for c in text)
        if has_cjk:
            data = cls._get_cjk_font_data()
            if data:
                return _f.Font(fontbuffer=data), True
        fname = cls._map_font(pdf_font_name)
        try:
            return _f.Font(fontname=fname), False
        except Exception:
            return _f.Font(fontname="helv"), False

    @classmethod
    def _optimal_font_size(cls, text, font_obj, avail_w, avail_h, orig_size):
        """Binary-search for the largest font size that fits text in the given area.

        Uses pixel-exact text_length() measurements.
        Height formula: n_lines * fontsize * lineheight, which matches how
        TextWriter.fill_textbox() stacks lines internally.
        """
        lo, hi = 4.0, float(orig_size)
        best = lo

        while lo <= hi:
            fs = (lo + hi) / 2.0
            lines = cls._wrap_text_pixel(text, font_obj, fs, avail_w)
            total_h = len(lines) * fs * 1.35 + 2  # +2pt fudge for ascender gap

            if total_h <= avail_h:
                best = fs
                lo = fs + 0.5
            else:
                hi = fs - 0.5

        return best

    @classmethod
    def _write_block_with_textwriter(cls, page, rect, text, font_obj, font_size, color, redact=True):
        """Write a text block using TextWriter.fill_textbox() for precise layout.

        Clears the area first with redaction annotations (unless redact=False),
        then uses TextWriter for HarfBuzz-powered text layout.
        """
        fitz = _get_fitz()

        if redact:
            # Clear original area
            page.add_redact_annot(rect)
            try:
                page.apply_redactions()
            except Exception:
                pass

        color_rgb = cls._int_to_rgb(color)

        # Try to write; if rect is too small for the font, shrink until it fits.
        for attempt in range(4):
            try:
                tw = fitz.TextWriter(page_rect=page.rect, color=color_rgb)
                tw.fill_textbox(
                    rect,
                    text,
                    font=font_obj,
                    fontsize=font_size,
                    lineheight=1.35,
                    align=fitz.TEXT_ALIGN_LEFT,
                )
                page.write_text(writers=tw)
                break
            except Exception:
                font_size *= 0.7
                if attempt >= 2:
                    # Last resort: write at minimum size (2pt)
                    try:
                        tw2 = fitz.TextWriter(page_rect=page.rect, color=color_rgb)
                        tw2.fill_textbox(
                            rect,
                            text,
                            font=font_obj,
                            fontsize=2,
                            lineheight=1.0,
                            align=fitz.TEXT_ALIGN_LEFT,
                        )
                        page.write_text(writers=tw2)
                    except Exception:
                        pass
                    break

    @classmethod
    def _write_rotated_block(cls, page, frag, text, font_obj, font_size, color):
        """Write rotated text preserving the original line direction.

        TextWriter.fill_textbox() doesn't support rotation, so we use
        page.insert_text() with the rotate parameter for rotated text.
        """
        import math
        fitz = _get_fitz()

        dir_vec = frag.meta.get("dir", (1.0, 0.0))
        # Check if text is rotated (dir differs from horizontal)
        if abs(dir_vec[0] - 1.0) < 0.001 and abs(dir_vec[1]) < 0.001:
            return False  # not rotated, caller should use TextWriter

        # Calculate rotation angle in degrees
        angle_rad = math.atan2(dir_vec[1], dir_vec[0])
        angle_deg = math.degrees(angle_rad)

        # Use first span's origin as insertion point
        spans = frag.meta["spans"]
        if spans:
            origin = spans[0].get("origin", [0, 0])
        else:
            origin = [frag.meta["bbox"][0], frag.meta["bbox"][1]]
        point = fitz.Point(origin[0], origin[1])

        # Clear original area
        rect = fitz.Rect(frag.meta["bbox"])
        page.add_redact_annot(rect)
        try:
            page.apply_redactions()
        except Exception:
            pass

        color_rgb = cls._int_to_rgb(color)

        # Build font name for insert_textbox
        has_cjk = any('一' <= c <= '鿿' for c in text)
        if has_cjk:
            cjk_data = cls._get_cjk_font_data()
            if cjk_data:
                cjk_name = "F0"
                try:
                    page.insert_font(fontname=cjk_name, fontbuffer=cjk_data)
                except Exception:
                    pass
                fontname = cjk_name
            else:
                fontname = "helv"
        else:
            fontname = font_obj.name if hasattr(font_obj, 'name') else "helv"

        # Build rotation matrix from direction vector.
        # dir = (cos_θ, sin_θ) → counter-clockwise rotation matrix:
        # [cosθ  -sinθ  0]
        # [sinθ   cosθ  0]
        # [0      0      1]
        cos_a, sin_a = dir_vec[0], dir_vec[1]
        rot_matrix = fitz.Matrix(cos_a, -sin_a, sin_a, cos_a, 0, 0)

        # Try morph=(point, matrix) first (PyMuPDF 1.27+), fall back to
        # morph=matrix and horizontal text if the tuple form isn't accepted.
        wrote = False
        for morph_arg in ((point, rot_matrix), rot_matrix):
            try:
                page.insert_textbox(
                    fitz.Rect(point.x, point.y, point.x + 800, point.y + 200),
                    text,
                    fontname=fontname,
                    fontsize=font_size,
                    color=color_rgb,
                    morph=morph_arg,
                )
                wrote = True
                break
            except Exception:
                continue

        if not wrote:
            # Last resort: write horizontally (no rotation)
            page.insert_text(
                point, text,
                fontname=fontname,
                fontsize=font_size,
                color=color_rgb,
            )
        return True

    # ── Utility ─────────────────────────────────────────────────────

    @staticmethod
    def _page_column_bounds(page_frags):
        """Given a list of (fragment, translation) pairs for one page,
        estimate the left/right text column boundaries.

        Takes the 5th-to-95th-percentile span of bbox x0/x1 values to
        ignore outliers (page numbers, side notes, full-width ruling
        lines).  Returns (col_left, col_right).
        """
        x0s = [f.meta["bbox"][0] for f in page_frags]
        x1s = [f.meta["bbox"][2] for f in page_frags]
        if not x0s:
            return 36, 563  # sensible defaults for A4/Letter
        x0s.sort()
        x1s.sort()
        n = len(x0s)
        p05 = max(0, int(n * 0.05))
        p95 = min(n - 1, int(n * 0.95))
        col_left = x0s[p05] if x0s else 36
        col_right = x1s[p95] if x1s else 563
        return col_left, col_right

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

    # CJK font data cache — used by _build_font_obj() via fontbuffer=
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

    @staticmethod
    def _map_font(pdf_font_name):
        """Map PDF font name to PyMuPDF built-in font or CJK font path."""
        """Map PDF font name to PyMuPDF built-in font."""
        if not pdf_font_name:
            return "helv"
        lower = pdf_font_name.lower()
        if "bold" in lower and "italic" in lower:
            return "hebi"  # Helvetica Bold-Italic
        if "bold" in lower:
            return "hebo"  # Helvetica Bold
        if "italic" in lower or "oblique" in lower:
            return "heit"  # Helvetica Italic
        if "mono" in lower or "courier" in lower or "consolas" in lower:
            return "cobo"
        if "serif" in lower or "times" in lower or "georgia" in lower:
            return "serif"
        return "helv"

    @classmethod
    def _wrap_text_pixel(cls, text, font_obj, font_size, max_width):
        """Wrap text using pixel-exact character widths from font.text_length().

        Handles CJK (any-char break), Latin (word break), and mixed text.
        Returns list of lines.
        """
        if not text or max_width <= 0:
            return [text] if text else []

        lines = []
        current_line = ""
        current_width = 0.0
        last_break = -1  # index of last safe break position
        last_break_width = 0.0

        i = 0
        while i < len(text):
            ch = text[i]

            # Determine pixel width of this character
            w = font_obj.text_length(ch, fontsize=font_size)

            # Track safe break positions
            if ch.isspace():
                # Space is a safe break point (after the space)
                last_break = i
                last_break_width = current_width + w if current_line else w

            cjk = '一' <= ch <= '鿿' or '぀' <= ch <= 'ヿ' or '가' <= ch <= '힯'

            # Check if adding this char exceeds max_width
            if current_width + w > max_width and current_line:
                if last_break >= 0 and last_break > (i - len(current_line)):
                    # Roll back to last break point
                    break_text = current_line[:last_break - (i - len(current_line))]
                    lines.append(break_text)
                    current_line = current_line[last_break - (i - len(current_line)):].lstrip()
                    current_width = sum(font_obj.text_length(c, fontsize=font_size) for c in current_line)
                elif cjk:
                    # CJK: break at this character boundary
                    lines.append(current_line)
                    current_line = ch
                    current_width = w
                    i += 1
                    continue
                else:
                    # Latin: force break at current position
                    lines.append(current_line)
                    current_line = ch
                    current_width = w
                    i += 1
                    continue
                last_break = -1

            current_line += ch
            current_width += w
            i += 1

        if current_line:
            lines.append(current_line)

        return lines if lines else [text]
