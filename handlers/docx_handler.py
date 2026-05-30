"""DOCX handler — translate Word documents using python-docx.

Extracts paragraphs and table cells as individual fragments.
Rebuild preserves formatting (bold, italic, font size, color, etc.).
Does NOT add lang attributes — only preserves original format.
"""

import os
import shutil
from copy import deepcopy

from .base import BaseHandler, TextFragment

# Lazy import for faster startup when docx isn't used
_docx = None


def _get_docx():
    global _docx
    if _docx is None:
        import docx as _d
        _docx = _d
    return _docx


# ── Skip patterns for code-like content ────────────────────────────
import re
_CODE_PATTERNS = (
    re.compile(r'^\s*[{}\[\];]'),          # lines starting with { } [ ] ;
    re.compile(r'^\s*(import |from |def |class |if |for |while )'),  # Python
    re.compile(r'^\s*(public |private |void |int |string )'),        # Java/C#
    re.compile(r'^\s*(#include|using namespace)'),                    # C++
    re.compile(r'^\s*<[^>]+>'),             # HTML/XML tags
    re.compile(r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE)\s', re.I),  # SQL
)


def _is_code_like(text: str) -> bool:
    """Heuristic: does this text look like code rather than prose?"""
    if not text or len(text.strip()) < 3:
        return False
    lines = text.strip().split('\n')
    if len(lines) > 2:
        code_lines = sum(1 for line in lines if any(p.search(line) for p in _CODE_PATTERNS))
        if code_lines / len(lines) > 0.4:
            return True
    return False


def _is_translatable(text: str) -> bool:
    """Check if text is worth translating (has actual language content)."""
    stripped = text.strip()
    if not stripped or len(stripped) < 2:
        return False
    if not re.search(r'[a-zA-Z]', stripped):
        return False
    if _is_code_like(stripped):
        return False
    return True


# ── Handler ────────────────────────────────────────────────────────

class DocxHandler(BaseHandler):
    """Handler for .docx Word documents."""

    @staticmethod
    def supported_extensions() -> list[str]:
        return [".docx"]

    def extract(self, file_path: str, skip_tags: list[str] = None,
                bilingual: bool = True) -> list[TextFragment]:
        """Extract translatable fragments from a .docx file.

        Strategy: iterate paragraphs and table cells. For each, extract
        the full text. Store a reference path (index-based) in meta
        so rebuild can find the exact element to modify.
        """
        docx = _get_docx()
        doc = docx.Document(file_path)
        fragments = []

        # 1. Extract paragraphs
        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not _is_translatable(text):
                continue
            fragments.append(TextFragment(
                text=text,
                meta={"type": "paragraph", "index": i},
            ))

        # 2. Extract table cells
        for ti, table in enumerate(doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    text = cell.text.strip()
                    if not _is_translatable(text):
                        continue
                    # Avoid duplicates: only extract from the first cell
                    # in a merged cell group
                    if cell._tc != row.cells[ci]._tc:
                        continue
                    fragments.append(TextFragment(
                        text=text,
                        meta={"type": "cell", "table": ti, "row": ri, "col": ci},
                    ))

        return fragments

    def rebuild(self, file_path: str, fragments: list[TextFragment],
                translations: list[str], bilingual: bool,
                target_lang: str = "zh-CN") -> str:
        """Write translations back to the document.

        Performance notes:
        - Paragraphs: iterate once, match by index, insert after if bilingual
        - Tables: iterate cells, write directly (no insert needed)
        - Formatting: copy runs from original to translated paragraph
        """
        docx = _get_docx()

        # Work on a copy
        output_path = self._output_path(file_path)
        shutil.copy2(file_path, output_path)

        doc = docx.Document(output_path)

        # Build translation lookup: meta_key → translation
        trans_map = {}
        for frag, trans in zip(fragments, translations):
            key = self._meta_key(frag.meta)
            trans_map[key] = trans

        # 1. Process paragraphs (reverse order for safe insertion)
        para_inserts = []  # (insert_after_index, translation, style)
        for i, para in enumerate(doc.paragraphs):
            key = f"paragraph_{i}"
            if key not in trans_map:
                continue
            trans = trans_map[key]
            if bilingual:
                # Record insertion point (do it after the loop to avoid index shift)
                para_inserts.append((para, trans))
            else:
                # Replace original text, keep first run's formatting
                self._replace_para_text(para, trans)

        # Insert translated paragraphs (reverse to preserve indices)
        for para, trans in reversed(para_inserts):
            new_para = self._insert_para_after(para, trans)

        # 2. Process tables
        for ti, table in enumerate(doc.tables):
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    key = f"cell_{ti}_{ri}_{ci}"
                    if key not in trans_map:
                        continue
                    trans = trans_map[key]
                    if bilingual:
                        # Append to cell: keep original, add translation below
                        self._append_cell_translation(cell, trans)
                    else:
                        # Replace cell content
                        self._replace_cell_text(cell, trans)

        doc.save(output_path)
        return output_path

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _meta_key(meta: dict) -> str:
        if meta["type"] == "paragraph":
            return f"paragraph_{meta['index']}"
        return f"cell_{meta['table']}_{meta['row']}_{meta['col']}"

    @staticmethod
    def _output_path(file_path: str) -> str:
        base, ext = os.path.splitext(file_path)
        return f"{base}_translated{ext}"

    @staticmethod
    def _replace_para_text(para, text: str):
        """Replace paragraph text, keeping the first run's formatting."""
        if not para.runs:
            para.text = text
            return
        # Keep formatting of first run
        first_run = para.runs[0]
        for run in para.runs[1:]:
            run.text = ""
        first_run.text = text

    @staticmethod
    def _insert_para_after(para, text: str):
        """Insert a new paragraph after the given one, copying its style."""
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        new_para = OxmlElement("w:p")
        # Copy style from original
        if para.paragraph_format.style:
            pPr = new_para.find(qn("w:pPr"))
            if pPr is None:
                pPr = OxmlElement("w:pPr")
                new_para.insert(0, pPr)
            pStyle = OxmlElement("w:pStyle")
            pStyle.set(qn("w:val"), para.paragraph_format.style.name)
            pPr.append(pStyle)

        # Add text run
        run = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        run.append(rPr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        run.append(t)
        new_para.append(run)

        # Insert after the original paragraph
        para._p.addnext(new_para)

        # Return a paragraph-like wrapper for potential chaining
        from docx.text.paragraph import Paragraph
        return Paragraph(new_para, para._parent)

    @staticmethod
    def _append_cell_translation(cell, text: str):
        """Append translation to a table cell with a line break."""
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        # Add a new paragraph in the cell
        p = OxmlElement("w:p")
        run = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        run.append(t)
        p.append(run)
        cell._tc.append(p)

    @staticmethod
    def _replace_cell_text(cell, text: str):
        """Replace cell content with translated text."""
        for para in cell.paragraphs:
            for run in para.runs:
                run.text = ""
        if cell.paragraphs:
            cell.paragraphs[0].runs[0].text = text if cell.paragraphs[0].runs else ""
            if not cell.paragraphs[0].runs:
                cell.paragraphs[0].text = text
