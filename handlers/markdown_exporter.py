"""Markdown exporter — converts translated content to Markdown files.

Handles three formats:
  EPUB  → read translated HTML from extract_dir, one .md per chapter
  DOCX  → build from fragment metadata (styles, tables)
  PDF   → Native: position-sorted fragments; BabelDOC/pdf2zh: re-extract

All exports are TARGET-LANGUAGE ONLY (even when translation was bilingual).
Output goes to: output/markdown/<book_name>/
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

OUTPUT_BASE = "markdown"


class MarkdownExporter:
    """Export translated documents to target-language-only Markdown."""

    # ── EPUB ─────────────────────────────────────────────────────

    @staticmethod
    def export_epub(
        extract_dir: str,
        output_dir: str,
        book_name: str,
    ) -> str:
        """Read translated HTML from extract_dir, write one .md per chapter.

        Returns the output directory path.
        """
        from epub_translator.extractor import EpubExtractor

        md_dir = os.path.join(output_dir, OUTPUT_BASE, book_name)
        os.makedirs(md_dir, exist_ok=True)

        # Find content files using the same logic as the translator
        # We need a dummy extractor just for list_content_files
        extractor = _make_extractor(extract_dir)
        content_files = extractor.list_content_files()

        chapter_count = 0
        for file_path in content_files:
            basename = os.path.basename(file_path)
            # Skip OPF, NCX, nav files
            if basename.lower() in ("content.opf", "toc.ncx", "navigation.xhtml"):
                continue
            if file_path.lower().endswith((".ncx", ".opf")):
                continue

            try:
                md_text = _html_to_markdown(file_path)
                if not md_text.strip():
                    continue
            except Exception:
                logger.debug("Skipping non-content file: %s", basename)
                continue

            chapter_count += 1
            stem = os.path.splitext(basename)[0]
            md_path = os.path.join(md_dir, f"{chapter_count:02d}_{stem}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_text)

        logger.info(
            "EPUB Markdown: %d chapters → %s", chapter_count, md_dir
        )
        print(f"\n[MARKDOWN] EPUB exported: {chapter_count} chapters → {os.path.abspath(md_dir)}\n")
        return md_dir

    # ── DOCX ─────────────────────────────────────────────────────

    @staticmethod
    def export_docx(
        fragments: list,
        translations: list[str],
        output_dir: str,
        book_name: str,
    ) -> str:
        """Build Markdown from DOCX fragment metadata.

        Uses meta.style to determine heading levels and meta.table
        coordinates to build Markdown tables.
        """
        md_dir = os.path.join(output_dir, OUTPUT_BASE, book_name)
        os.makedirs(md_dir, exist_ok=True)

        lines: list[str] = []
        table_rows: dict[int, dict[int, str]] = {}  # row → col → text
        current_section: str | None = None

        for frag, trans in zip(fragments, translations):
            meta = getattr(frag, "meta", {}) or {}
            ftype = meta.get("type", "paragraph")

            if ftype == "cell":
                # Collect table cells
                tn = meta.get("table", 0)
                rn = meta.get("row", 0)
                cn = meta.get("col", 0)
                key = (tn, rn, cn)
                if rn not in table_rows:
                    table_rows[rn] = {}
                table_rows[rn][cn] = trans
                continue

            # Flush pending table
            if table_rows:
                lines.append(_format_md_table(table_rows))
                lines.append("")
                table_rows = {}

            # Paragraph with style
            style = meta.get("style", "")
            text = trans.strip()
            if not text:
                continue

            # Heading
            if style.startswith("Heading"):
                try:
                    level = int(style.split()[-1])
                except (ValueError, IndexError):
                    level = 2
                level = min(level, 6)
                lines.append(f"{'#' * level} {text}")
                lines.append("")
                current_section = text
            elif meta.get("is_toc"):
                # Skip TOC entries (redundant with headings)
                continue
            else:
                lines.append(text)
                lines.append("")

        # Flush remaining table
        if table_rows:
            lines.append(_format_md_table(table_rows))
            lines.append("")

        md_path = os.path.join(md_dir, f"{book_name}.md")
        content = "\n".join(lines).strip() + "\n"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("DOCX Markdown: %s", md_path)
        return md_dir

    # ── PDF (Native PyMuPDF) ─────────────────────────────────────

    @staticmethod
    def export_pdf_native(
        fragments: list,
        translations: list[str],
        output_dir: str,
        book_name: str,
    ) -> str:
        """Build Markdown from PDF fragments sorted by page + Y position."""
        md_dir = os.path.join(output_dir, OUTPUT_BASE, book_name)
        os.makedirs(md_dir, exist_ok=True)

        # Group fragments by page
        pages: dict[int, list[dict]] = {}
        for frag, trans in zip(fragments, translations):
            meta = getattr(frag, "meta", {}) or {}
            page = meta.get("page", 1)
            # Get Y position from bbox
            bbox = meta.get("bbox", [0, 0, 0, 0])
            y = bbox[1] if len(bbox) > 1 else 0

            if page not in pages:
                pages[page] = []
            pages[page].append({
                "text": trans.strip(),
                "y": y,
            })

        lines: list[str] = []
        for pn in sorted(pages.keys()):
            if pn > 1:
                lines.append("")
                lines.append("---")
                lines.append("")

            # Sort by Y descending (top of page first)
            page_frags = sorted(pages[pn], key=lambda f: -f["y"])
            for f in page_frags:
                if f["text"]:
                    lines.append(f["text"])
                    lines.append("")

        md_path = os.path.join(md_dir, f"{book_name}.md")
        content = "\n".join(lines).strip() + "\n"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("PDF Native Markdown: %s", md_path)
        return md_dir

    # ── PDF (BabelDOC / pdf2zh post-hoc) ─────────────────────────

    @staticmethod
    def export_pdf_post_hoc(
        pdf_path: str,
        output_dir: str,
        book_name: str,
    ) -> str:
        """Re-extract text from a translated PDF using PyMuPDF.

        Used for BabelDOC and pdf2zh where the internal pipeline
        doesn't expose translated text.
        """
        import fitz

        md_dir = os.path.join(output_dir, OUTPUT_BASE, book_name)
        os.makedirs(md_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        lines: list[str] = []

        for i in range(len(doc)):
            if i > 0:
                lines.append("")
                lines.append("---")
                lines.append("")
            text = doc[i].get_text().strip()
            if text:
                lines.append(text)
                lines.append("")

        doc.close()

        md_path = os.path.join(md_dir, f"{book_name}.md")
        content = "\n".join(lines).strip() + "\n"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("PDF post-hoc Markdown: %s", md_path)
        return md_dir


# ── Internal helpers ────────────────────────────────────────────────


def _make_extractor(extract_dir: str):
    """Create a minimal EpubExtractor from an existing extract_dir."""
    from epub_translator.extractor import EpubExtractor

    # Reconstruct the source EPUB path (not needed for list_content_files)
    # We just need the extract_dir set
    extractor = EpubExtractor.__new__(EpubExtractor)
    extractor._extract_dir = extract_dir
    # Set a dummy epub path — extract() won't be called
    extractor._epub_path = os.path.join(extract_dir, "dummy.epub")
    extractor._book_name = os.path.basename(extract_dir)
    return extractor


def _html_to_markdown(file_path: str) -> str:
    """Convert a translated HTML/XHTML file to Markdown.

    Outputs whatever the translated HTML contains — bilingual mode
    keeps both languages, chinese_only has only the target language.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return ""

    # Try multiple parsers — XHTML from "xml" parser may fail with "lxml"
    soup = None
    for parser in ("lxml", "html.parser", "xml"):
        try:
            s = BeautifulSoup(content, parser)
            body = s.find("body")
            text = body.get_text(strip=True) if body else s.get_text(strip=True)
            if len(text) > 20:  # real content found
                soup = s
                break
        except Exception:
            continue

    if soup is None:
        # All parsers failed — just strip HTML tags
        import re
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        return text + "\n" if text else ""

    # Remove script/style
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    body = soup.find("body")
    if body is None:
        body = soup

    lines: list[str] = []
    _html_to_md_lines(body, lines)
    result = "\n\n".join(lines).strip()

    # Fallback: if structured conversion produced nothing, dump all text
    if not result:
        text = body.get_text(separator="\n", strip=True)
        result = text

    return result + "\n" if result else ""


def _html_to_md_lines(elem, lines: list[str]):
    """Walk HTML tree and produce Markdown-formatted lines."""
    for child in elem.children if hasattr(elem, "children") else []:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                lines.append(text)
            continue

        if not hasattr(child, "name"):
            continue

        tag = child.name.lower() if child.name else ""

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = child.get_text(strip=True)
            if text:
                lines.append(f"{'#' * level} {text}")

        elif tag == "p":
            text = _inline_format(child)
            if text:
                lines.append(text)

        elif tag in ("li",):
            text = _inline_format(child)
            if text:
                lines.append(f"- {text}")

        elif tag == "table":
            _table_to_md(child, lines)

        elif tag in ("blockquote",):
            for p in child.find_all("p"):
                text = _inline_format(p)
                if text:
                    lines.append(f"> {text}")

        elif tag == "img":
            alt = child.get("alt", "")
            src = child.get("src", "")
            if alt or src:
                lines.append(f"![{alt}]({src})")

        elif tag in ("pre", "code"):
            text = child.get_text()
            if text.strip():
                lines.append(f"```\n{text.strip()}\n```")

        elif tag in ("a",):
            href = child.get("href", "")
            text = child.get_text(strip=True)
            if text and href:
                lines.append(f"[{text}]({href})")
            elif text:
                lines.append(text)

        elif tag in ("strong", "b"):
            text = child.get_text(strip=True)
            if text:
                lines.append(f"**{text}**")

        elif tag in ("em", "i"):
            text = child.get_text(strip=True)
            if text:
                lines.append(f"*{text}*")

        elif tag in ("br", "hr"):
            pass  # ignore

        elif tag in ("div", "section", "article", "span", "body", "html", "head",
                      "ol", "ul", "thead", "tbody", "tr", "th", "td",
                      "colgroup", "col", "nav", "figure", "figcaption",
                      "header", "footer", "main", "aside"):
            # Container elements — recurse into children
            _html_to_md_lines(child, lines)

        else:
            # Unknown element — just get its text
            _html_to_md_lines(child, lines)


def _inline_format(elem) -> str:
    """Get text from an inline element with basic formatting preserved."""
    result = []
    for child in elem.children if hasattr(elem, "children") else []:
        if isinstance(child, NavigableString):
            result.append(str(child))
        elif hasattr(child, "name"):
            tag = child.name.lower() if child.name else ""
            if tag in ("strong", "b"):
                result.append(f"**{child.get_text()}**")
            elif tag in ("em", "i"):
                result.append(f"*{child.get_text()}*")
            elif tag in ("code",):
                result.append(f"`{child.get_text()}`")
            elif tag == "a":
                href = child.get("href", "")
                text = child.get_text(strip=True)
                if href and not href.startswith("#"):
                    result.append(f"[{text}]({href})")
                else:
                    result.append(text)
            elif tag == "br":
                result.append("\n")
            elif tag == "img":
                alt = child.get("alt", "")
                src = child.get("src", "")
                result.append(f"![{alt}]({src})")
            elif tag in ("span", "sub", "sup"):
                result.append(child.get_text())
            else:
                result.append(child.get_text())
        else:
            result.append(str(child))
    text = "".join(result).strip()
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def _table_to_md(elem, lines: list[str]):
    """Convert an HTML table to Markdown table format."""
    rows = elem.find_all("tr")
    if not rows:
        return

    # Collect data
    data: list[list[str]] = []
    max_cols = 0
    for row in rows:
        cells = row.find_all(["td", "th"])
        row_data = [cell.get_text(strip=True) for cell in cells]
        max_cols = max(max_cols, len(row_data))
        if row_data:
            data.append(row_data)

    if not data:
        return

    for i, row_data in enumerate(data):
        padded = row_data + [""] * (max_cols - len(row_data))
        lines.append("| " + " | ".join(padded) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * max_cols) + " |")


def _format_md_table(table_rows: dict[int, dict[int, str]]) -> str:
    """Format a table from {row_num: {col_num: text}} dict."""
    if not table_rows:
        return ""
    max_row = max(table_rows.keys())
    max_col = max(max(cells.keys()) for cells in table_rows.values()) if table_rows else 0

    lines = [""]
    for rn in sorted(table_rows.keys()):
        row = []
        for cn in range(max_col + 1):
            row.append(table_rows[rn].get(cn, ""))
        lines.append("| " + " | ".join(row) + " |")
        if rn == min(table_rows.keys()):
            lines.append("| " + " | ".join(["---"] * (max_col + 1)) + " |")
    lines.append("")
    return "\n".join(lines)
