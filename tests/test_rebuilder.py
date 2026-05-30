import os
import shutil
import tempfile
import pytest

from epub_translator.rebuilder import rebuild_epub


class TestRebuilder:
    @pytest.fixture
    def extract_dir(self):
        """Create a minimal extracted EPUB directory structure."""
        tmp = tempfile.mkdtemp()
        # mimetype
        with open(os.path.join(tmp, "mimetype"), "w") as f:
            f.write("application/epub+zip")
        # META-INF
        os.makedirs(os.path.join(tmp, "META-INF"))
        with open(os.path.join(tmp, "META-INF", "container.xml"), "w") as f:
            f.write("<container/>")
        # OEBPS
        os.makedirs(os.path.join(tmp, "OEBPS"))
        with open(os.path.join(tmp, "OEBPS", "content.opf"), "w") as f:
            f.write("<package/>")
        with open(os.path.join(tmp, "OEBPS", "chapter1.html"), "w") as f:
            f.write("<html><body><p>Hello</p></body></html>")
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_rebuild_creates_epub(self, extract_dir):
        import zipfile
        output_dir = tempfile.mkdtemp()
        result = rebuild_epub(extract_dir, output_dir, "testbook")
        assert os.path.exists(result)
        assert result.endswith("testbook_zh.epub")

        # Verify it's a valid ZIP
        assert zipfile.is_zipfile(result)

        # Verify mimetype is first entry
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert names[0] == "mimetype"

            # Verify mimetype is stored (not compressed)
            info = zf.getinfo("mimetype")
            assert info.compress_type == zipfile.ZIP_STORED

        shutil.rmtree(output_dir, ignore_errors=True)

    def test_rebuild_includes_all_files(self, extract_dir):
        import zipfile
        output_dir = tempfile.mkdtemp()
        result = rebuild_epub(extract_dir, output_dir, "testbook")

        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "mimetype" in names
            assert "META-INF/container.xml" in names
            assert "OEBPS/content.opf" in names
            assert "OEBPS/chapter1.html" in names

        shutil.rmtree(output_dir, ignore_errors=True)

    def test_rebuild_missing_mimetype_raises(self):
        tmp = tempfile.mkdtemp()
        output_dir = tempfile.mkdtemp()
        with pytest.raises(FileNotFoundError, match="mimetype"):
            rebuild_epub(tmp, output_dir, "badbook")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
