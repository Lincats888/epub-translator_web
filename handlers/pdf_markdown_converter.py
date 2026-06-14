"""PDF → Markdown converter using BabelDOC's IL character extraction.

Uses BabelDOC's parse_only API to extract raw characters with positions,
then clusters them into lines and paragraphs using position heuristics.

Usage:
    converter = PdfMarkdownConverter()
    md = converter.convert("paper.pdf")
    md = converter.convert("paper.pdf", pages="1-5,8")
    converter.convert_to_file("paper.pdf", "output.md")
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Clustering constants ────────────────────────────────────────

LINE_Y_TOLERANCE_FACTOR = 0.6   # fraction of median font size for line grouping
PARA_GAP_FACTOR = 1.8           # gap > factor * line_spacing → paragraph break
HEADING_FONT_RATIO = 1.3        # font size ratio vs median → heading candidate
DEFAULT_FONT_SIZE = 10.0


class PdfMarkdownConverter:
    """Convert a text-layer PDF to Markdown via BabelDOC's IL."""

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────────

    def convert(
        self,
        pdf_path: str | Path,
        pages: str | None = None,
        working_dir: str | Path | None = None,
    ) -> str:
        """Convert PDF to a single Markdown string."""
        from babeldoc.format.pdf.parse_only import (
            parse_with_new_parser_to_legacy_ir,
        )
        from babeldoc.format.pdf.document_il import il_version_1

        pdf_path = str(pdf_path)
        if working_dir is None:
            working_dir = os.path.dirname(os.path.abspath(pdf_path)) or "."

        logger.info("Parsing PDF IL: %s", pdf_path)
        doc: il_version_1.Document = parse_with_new_parser_to_legacy_ir(
            pdf_path,
            pages=pages,
            working_dir=str(working_dir),
            debug=False,
        )

        return self._document_to_markdown(doc)

    def convert_to_file(
        self,
        pdf_path: str | Path,
        output_path: str | Path,
        pages: str | None = None,
        working_dir: str | Path | None = None,
    ) -> str:
        """Convert PDF to Markdown and write to a file."""
        md = self.convert(pdf_path, pages=pages, working_dir=working_dir)
        output_path = str(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info("Markdown written to %s (%d chars)", output_path, len(md))
        return os.path.abspath(output_path)

    # ── Document traversal ──────────────────────────────────────

    def _document_to_markdown(self, doc) -> str:
        """Walk Document → Pages → character clusters → Markdown."""
        all_lines: list[str] = []

        for pi, page in enumerate(doc.page):
            pn = page.page_number or (pi + 1)

            # Build font lookup: font_id → font info
            fonts: dict[str, object] = {}
            if page.pdf_font:
                for f in page.pdf_font:
                    if f.font_id:
                        fonts[f.font_id] = f

            # Collect characters with metadata
            chars = self._collect_chars(page, fonts)
            if not chars:
                continue

            # Cluster into lines
            raw_lines = self._cluster_lines(chars)

            # Merge into paragraphs
            page_md = self._lines_to_markdown(raw_lines, pn, pi > 0)
            if page_md:
                all_lines.append(page_md)

        return "\n\n".join(all_lines).strip() + "\n"

    # ── Character collection ────────────────────────────────────

    def _collect_chars(self, page, fonts: dict) -> list[dict]:
        """Collect all characters from a page with position and style info."""
        chars = []
        for ch in page.pdf_character or []:
            c = ch.char_unicode
            if c is None or c == "":
                continue
            box = ch.box
            if box is None:
                continue

            style = ch.pdf_style
            font_size = style.font_size if style else DEFAULT_FONT_SIZE
            font_id = style.font_id if style else None
            font = fonts.get(font_id) if font_id else None

            chars.append({
                "char": c,
                "x": box.x or 0,
                "y": box.y or 0,
                "x2": box.x2 or 0,
                "font_size": font_size or DEFAULT_FONT_SIZE,
                "bold": getattr(font, "bold", None) if font else None,
                "italic": getattr(font, "italic", None) if font else None,
                "mono": getattr(font, "monospace", None) if font else None,
                "render_order": ch.render_order or 0,
            })
        return chars

    # ── Line clustering ─────────────────────────────────────────

    def _cluster_lines(self, chars: list[dict]) -> list[dict]:
        """Cluster characters into lines based on Y-position.

        PDF y=0 is at page bottom, so we sort DESCENDING by y
        to read top-to-bottom.  Returns lines sorted by y descending.
        """
        if not chars:
            return []

        # Sort by render order then position
        chars.sort(key=lambda c: (c["render_order"], -c["y"], c["x"]))

        # Compute median font size for tolerance
        sizes = [c["font_size"] for c in chars]
        sizes.sort()
        median_size = sizes[len(sizes) // 2] if sizes else DEFAULT_FONT_SIZE
        y_tol = median_size * LINE_Y_TOLERANCE_FACTOR

        # Cluster by Y
        clusters: list[list[dict]] = []
        current: list[dict] = []
        current_y = chars[0]["y"]

        for ch in chars:
            if abs(ch["y"] - current_y) > y_tol and current:
                clusters.append(current)
                current = []
                current_y = ch["y"]
            current.append(ch)

        if current:
            clusters.append(current)

        # Build line dicts
        lines = []
        for cluster in clusters:
            cluster.sort(key=lambda c: c["x"])
            text = self._join_chars_with_spaces(cluster)

            font_sizes = [c["font_size"] for c in cluster]
            avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else DEFAULT_FONT_SIZE

            bold_count = sum(1 for c in cluster if c["bold"])
            italic_count = sum(1 for c in cluster if c["italic"])

            lines.append({
                "y": sum(c["y"] for c in cluster) / len(cluster),
                "y_min": min(c["y"] for c in cluster),
                "y_max": max(c["y"] for c in cluster),
                "font_size": avg_size,
                "text": text,
                "bold": bold_count > len(cluster) * 0.5,
                "italic": italic_count > len(cluster) * 0.5,
            })

        # Sort descending by y (top of page first, since y=0 is at bottom)
        lines.sort(key=lambda l: -l["y"])
        return lines

    @staticmethod
    def _join_chars_with_spaces(cluster: list[dict]) -> str:
        """Join characters, inserting spaces at word boundaries.

        A word boundary is detected when the gap between consecutive
        characters exceeds a threshold relative to the average advance.
        """
        if not cluster:
            return ""

        # Compute average advance width
        advances = []
        for i in range(1, len(cluster)):
            adv = cluster[i]["x"] - cluster[i - 1]["x2"]
            if adv > 0:
                advances.append(adv)
        avg_advance = sum(advances) / len(advances) if advances else 5.0
        space_threshold = avg_advance * 2.5  # gap > 2.5x avg advance → word break

        parts = [cluster[0]["char"]]
        for i in range(1, len(cluster)):
            gap = cluster[i]["x"] - cluster[i - 1]["x2"]
            # Also check: next char x > previous char x2 + threshold
            if gap > space_threshold:
                parts.append(" ")
            parts.append(cluster[i]["char"])

        return "".join(parts)

    # ── Lines → Markdown ────────────────────────────────────────

    def _lines_to_markdown(
        self,
        lines: list[dict],
        page_num: int,
        add_page_sep: bool = False,
    ) -> str:
        """Group lines into paragraphs, detect headings, output Markdown."""
        if not lines:
            return ""

        # Compute stats for heading detection
        sizes = [l["font_size"] for l in lines]
        sizes.sort()
        median_size = sizes[len(sizes) // 2] if sizes else DEFAULT_FONT_SIZE
        heading_threshold = max(median_size * HEADING_FONT_RATIO, median_size + 2)

        # Compute median line spacing for paragraph detection
        spacings = []
        for i in range(1, len(lines)):
            gap = lines[i]["y_min"] - lines[i - 1]["y_max"]
            if gap > 0:
                spacings.append(gap)
        median_spacing = sorted(spacings)[len(spacings) // 2] if spacings else median_size * 1.5
        para_threshold = median_spacing * PARA_GAP_FACTOR

        # Build Markdown
        output: list[str] = []
        if add_page_sep:
            output.append("")
            output.append("---")
            output.append("")

        pending_text: list[str] = []
        prev_line = None

        for line in lines:
            text = line["text"]
            if not text:
                continue

            # ── Heading detection ──
            if line["font_size"] >= heading_threshold:
                # Flush pending
                if pending_text:
                    output.append(" ".join(pending_text))
                    output.append("")
                    pending_text = []

                # Determine heading level
                ratio = line["font_size"] / max(median_size, 1)
                if ratio > 2.0:
                    level = 1
                elif ratio > 1.6:
                    level = 2
                else:
                    level = 3

                heading_text = text
                if line["bold"]:
                    heading_text = f"**{heading_text}**"
                output.append(f"{'#' * level} {heading_text}")
                output.append("")
                prev_line = line
                continue

            # ── Paragraph break detection ──
            if prev_line is not None:
                gap = line["y_min"] - prev_line["y_max"]
                if gap > para_threshold and pending_text:
                    output.append(" ".join(pending_text))
                    output.append("")
                    pending_text = []

            # Add line text
            styled = text
            if line["bold"]:
                styled = f"**{styled}**"
            if line["italic"]:
                styled = f"*{styled}*"
            pending_text.append(styled)
            prev_line = line

        # Flush remaining
        if pending_text:
            output.append(" ".join(pending_text))
            output.append("")

        return "\n".join(output).strip()
