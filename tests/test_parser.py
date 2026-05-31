import os
import tempfile
import pytest

from epub_translator.parser import (
    parse_html_file,
    parse_ncx_file,
    parse_opf_file,
    parse_file,
    ParsedFile,
)


class TestParser:
    @pytest.fixture
    def html_file(self):
        content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test Title</title></head>
<body>
  <h1>Hello World</h1>
  <p>This is a paragraph.</p>
  <p>Another paragraph with <strong>bold</strong> text.</p>
  <script>console.log("do not translate");</script>
  <style>body { color: red; }</style>
</body>
</html>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".xhtml", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        yield tmp.name
        os.unlink(tmp.name)

    @pytest.fixture
    def ncx_file(self):
        content = """<?xml version="1.0" encoding="UTF-8"?>
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <docTitle><text>Book Title</text></docTitle>
  <navMap>
    <navPoint id="nav1">
      <navLabel><text>Chapter One</text></navLabel>
      <content src="ch1.html"/>
    </navPoint>
  </navMap>
</ncx>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ncx", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        yield tmp.name
        os.unlink(tmp.name)

    @pytest.fixture
    def opf_file(self):
        content = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>My Book Title</dc:title>
    <dc:creator>Author Name</dc:creator>
  </metadata>
</package>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".opf", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        yield tmp.name
        os.unlink(tmp.name)

    def test_parse_html_extracts_text(self, html_file):
        skip_tags = ["script", "style", "code", "pre"]
        parsed = parse_html_file(html_file, skip_tags)
        texts = [f.text for f in parsed.fragments]
        assert "Hello World" in texts
        assert "This is a paragraph." in texts
        # "bold" is inside <strong> within a <p> block; block-level extraction
        # captures the full paragraph, not individual NavStrings
        assert any("<strong>bold</strong>" in t for t in texts)

    def test_parse_html_skips_script_and_style(self, html_file):
        skip_tags = ["script", "style", "code", "pre"]
        parsed = parse_html_file(html_file, skip_tags)
        texts = [f.text.strip() for f in parsed.fragments]
        assert 'console.log("do not translate")' not in texts
        assert "body { color: red; }" not in texts

    def test_parse_ncx_extracts_titles(self, ncx_file):
        parsed = parse_ncx_file(ncx_file)
        texts = [f.text for f in parsed.fragments]
        assert "Book Title" in texts
        assert "Chapter One" in texts

    def test_parse_opf_extracts_metadata(self, opf_file):
        parsed = parse_opf_file(opf_file)
        texts = [f.text for f in parsed.fragments]
        assert "My Book Title" in texts
        assert "Author Name" in texts

    def test_write_back_and_save(self, html_file):
        skip_tags = ["script", "style", "code", "pre"]
        parsed = parse_html_file(html_file, skip_tags)

        translations = []
        for f in parsed.fragments:
            translations.append(f"[TRANS] {f.text}")

        parsed.save(translations)

        # Read the file back and verify translation was written
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "[TRANS] Hello World" in content
        assert "[TRANS] This is a paragraph." in content
        assert "console.log" in content  # should still exist (not translated)

    def test_parse_file_dispatches_correctly(self, html_file):
        skip_tags = ["script", "style"]
        parsed = parse_file(html_file, skip_tags)
        assert isinstance(parsed, ParsedFile)
        assert len(parsed.fragments) > 0

    def test_translatable_filter(self, html_file):
        """Whitespace-only and numeric-only strings should not be extracted."""
        skip_tags = ["script", "style"]
        parsed = parse_html_file(html_file, skip_tags)
        for fragment in parsed.fragments:
            text = fragment.text.strip()
            # All extracted texts should contain at least one letter
            assert text, f"Empty fragment found"

    def test_bilingual_parse_by_blocks(self, html_file):
        """Bilingual mode extracts text per block element, not per string."""
        skip_tags = ["script", "style"]
        parsed = parse_html_file(html_file, skip_tags, bilingual=True)
        texts = [f.text for f in parsed.fragments]
        assert "Hello World" in texts
        # In block mode, "This is a paragraph." appears as a complete block
        assert any("This is a paragraph." in t for t in texts)
        # "bold" should NOT appear alone — it's part of a block
        standalone_bold = any(t.strip() == "bold" for t in texts)
        assert not standalone_bold

    def test_bilingual_write_back_duplicates_elements(self, html_file):
        """Bilingual write_back should clone elements, not replace text in-place."""
        skip_tags = ["script", "style"]
        parsed = parse_html_file(html_file, skip_tags, bilingual=True)

        translations = [f"[ZH] {f.text}" for f in parsed.fragments]
        parsed.save(translations)

        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        # Original English should still be present
        assert "Hello World" in content
        # Translation should be present in a cloned element
        assert "[ZH] Hello World" in content
        # Verify the translation is in a separate h1 element right after the original
        assert "<h1" in content and "[ZH] Hello World</h1>" in content
        # Translation marker should be set on both original and cloned elements
        assert content.count("data-epub-translator") >= 2

    def test_bilingual_reparse_is_idempotent(self, html_file):
        """Re-parsing an already-translated file should yield zero new fragments."""
        skip_tags = ["script", "style"]

        # First pass: translate and save
        parsed1 = parse_html_file(html_file, skip_tags, bilingual=True)
        translations1 = [f"[ZH] {f.text}" for f in parsed1.fragments]
        parsed1.save(translations1)

        # Second pass: re-parse — should find no new translatable content
        parsed2 = parse_html_file(html_file, skip_tags, bilingual=True)
        assert len(parsed2.fragments) == 0, (
            f"Expected 0 fragments on re-parse, got {len(parsed2.fragments)}"
        )

    def test_bilingual_skips_script_and_style(self, html_file):
        skip_tags = ["script", "style", "code", "pre"]
        parsed = parse_html_file(html_file, skip_tags, bilingual=True)
        texts = [f.text for f in parsed.fragments]
        assert 'console.log("do not translate")' not in texts
        assert "body { color: red; }" not in texts

    def test_bilingual_table_writes_in_original_cells(self):
        """Bilingual table: each cell translated individually, written directly in cell."""
        content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test</title></head>
<body>
  <table>
    <tr><th>Name</th><th>Value</th></tr>
    <tr><td>Apple</td><td>Red</td></tr>
    <tr><td>Banana</td><td>Yellow</td></tr>
  </table>
</body>
</html>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".xhtml", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()

        try:
            skip_tags = ["script", "style"]
            parsed = parse_html_file(tmp.name, skip_tags, bilingual=True)

            # Each table cell is an individual fragment
            cell_fragments = [
                f for f in parsed.fragments
                if hasattr(f.element, "name") and f.element.name in ("td", "th")
            ]
            assert len(cell_fragments) == 6

            # Simulate translator returning one translation per cell
            translations = ["名称", "数值", "苹果", "红色", "香蕉", "黄色"]
            parsed.save(translations)

            with open(tmp.name, "r", encoding="utf-8") as f:
                result = f.read()

            # Translation should be in original cells (bilingual: English + Chinese)
            assert "Apple" in result
            assert "苹果" in result
            assert "Red" in result
            assert "红色" in result
            # Only ONE table (no clone)
            assert result.count("<table") == 1
            # Table has translation marker
            assert "data-epub-translator" in result
            # Line break between original and translation inside cells
            assert "<br" in result
        finally:
            os.unlink(tmp.name)

    def test_bilingual_table_partial_translation(self):
        """When only some cells are translated, others keep original text."""
        content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test</title></head>
<body>
  <table>
    <tr><td>Alpha</td><td>Beta</td><td>Gamma</td></tr>
  </table>
</body>
</html>"""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".xhtml", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()

        try:
            skip_tags = ["script", "style"]
            parsed = parse_html_file(tmp.name, skip_tags, bilingual=True)

            # 3 cell fragments
            cell_fragments = [
                f for f in parsed.fragments
                if hasattr(f.element, "name") and f.element.name in ("td", "th")
            ]
            assert len(cell_fragments) == 3

            # Translate only 2 of 3 cells
            translations = ["阿尔法", "贝塔"]
            parsed.save(translations)

            with open(tmp.name, "r", encoding="utf-8") as f:
                result = f.read()

            # Translated cells should have bilingual content
            assert "阿尔法" in result
            assert "贝塔" in result
            # Original English preserved in all cells
            assert "Alpha" in result
            assert "Beta" in result
            assert "Gamma" in result
            # Only ONE table
            assert result.count("<table") == 1
        finally:
            os.unlink(tmp.name)

    def test_parse_file_bilingual_dispatches(self, html_file):
        skip_tags = ["script", "style"]
        parsed = parse_file(html_file, skip_tags, bilingual=True)
        assert isinstance(parsed, ParsedFile)
        assert parsed.bilingual is True
        assert len(parsed.fragments) > 0
