"""EPUB handler — wraps existing epub_translator package (zero modifications).

This handler delegates to the existing parsing and translation logic.
The only addition is the target_lang parameter for lang attribute support.
"""

import os
import sys
from pathlib import Path

# Ensure project root is on path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from epub_translator.parser import parse_file
from epub_translator.rebuilder import inject_line_height, rebuild_epub

from .base import BaseHandler, TextFragment


class EpubHandler(BaseHandler):
    """Handler for .epub files. Wraps existing epub_translator code."""

    @staticmethod
    def supported_extensions() -> list[str]:
        return [".epub"]

    def extract(self, file_path: str, skip_tags: list[str] = None,
                bilingual: bool = True) -> list[TextFragment]:
        # The EPUB extractor works at the directory level (temp/),
        # not individual files. For the server, we use parse_file directly.
        # The actual extract is done by EpubExtractor in server.py.
        # This method is for consistency — parse a single HTML/XHTML file.
        if skip_tags is None:
            skip_tags = ["script", "style", "code", "pre"]

        parsed = parse_file(file_path, skip_tags, bilingual=bilingual)
        fragments = []
        for f in parsed.fragments:
            fragments.append(TextFragment(
                text=f.text,
                meta={"element": f, "parsed": parsed},
            ))
        return fragments

    def rebuild(self, file_path: str, fragments: list[TextFragment],
                translations: list[str], bilingual: bool,
                target_lang: str = "zh-CN") -> str:
        """For EPUB, rebuild is handled by the full pipeline in server.py."""
        raise NotImplementedError(
            "EPUB rebuild uses the full pipeline (extract_dir → rebuild_epub). "
            "Use the server's _run_translate function instead."
        )
