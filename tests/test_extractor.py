import os
import shutil
import pytest

from epub_translator.extractor import EpubExtractor


class TestEpubExtractor:
    @pytest.fixture
    def epub_path(self):
        return "tests/fixtures/sample.epub"

    def test_extract_creates_directory(self, epub_path):
        temp_dir = "temp/test_extract_create"
        extractor = EpubExtractor(epub_path, temp_dir)
        result = extractor.extract()
        assert os.path.isdir(result)
        assert os.path.exists(os.path.join(result, "mimetype"))
        assert os.path.exists(os.path.join(result, "META-INF", "container.xml"))
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_extract_idempotent(self, epub_path):
        temp_dir = "temp/test_extract_idempotent"
        extractor1 = EpubExtractor(epub_path, temp_dir)
        dir1 = extractor1.extract()
        extractor2 = EpubExtractor(epub_path, temp_dir)
        dir2 = extractor2.extract()
        assert dir1 == dir2
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_is_extracted(self, epub_path):
        temp_dir = "temp/test_is_extracted"
        extractor = EpubExtractor(epub_path, temp_dir)
        assert not extractor.is_extracted()
        extractor.extract()
        assert extractor.is_extracted()
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_opf_path(self, epub_path):
        temp_dir = "temp/test_opf"
        extractor = EpubExtractor(epub_path, temp_dir)
        extractor.extract()
        opf = extractor.get_opf_path()
        assert os.path.exists(opf)
        assert opf.endswith("content.opf")
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_list_content_files(self, epub_path):
        temp_dir = "temp/test_list_files"
        extractor = EpubExtractor(epub_path, temp_dir)
        extractor.extract()
        files = extractor.list_content_files()
        assert len(files) >= 2
        html_files = [f for f in files if f.endswith(".html")]
        assert len(html_files) >= 2
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_find_toc_file(self, epub_path):
        temp_dir = "temp/test_toc"
        extractor = EpubExtractor(epub_path, temp_dir)
        extractor.extract()
        toc = extractor.find_toc_file()
        assert toc is not None
        assert "toc.ncx" in toc
        shutil.rmtree(temp_dir, ignore_errors=True)
