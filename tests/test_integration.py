"""Integration tests for the full EPUB translation pipeline."""

import os
import shutil
import zipfile
from unittest.mock import Mock, patch

from epub_translator.config import Config
from epub_translator.extractor import EpubExtractor
from epub_translator.cache import TranslationCache
from epub_translator.parser import parse_file
from epub_translator.translator import Translator
from epub_translator.rebuilder import rebuild_epub


class TestIntegration:
    def test_full_pipeline_with_mock_api(self):
        """Test the entire pipeline end-to-end with a mocked DeepSeek API."""
        epub_path = "tests/fixtures/sample.epub"
        temp_dir = "temp/test_integration_full"
        output_dir = "output/test_integration_full"

        # Clean up from previous runs
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)

        try:
            # 1. Extract
            extractor = EpubExtractor(epub_path, temp_dir)
            extract_dir = extractor.extract()
            assert os.path.isdir(extract_dir)
            assert os.path.exists(os.path.join(extract_dir, "mimetype"))

            # 2. Load cache
            cache = TranslationCache(extract_dir)
            cache.load()

            # 3. Collect content files
            content_files = extractor.list_content_files()
            toc_file = extractor.find_toc_file()
            if toc_file and toc_file not in content_files:
                content_files.append(toc_file)
            opf_file = extractor.get_opf_path()
            if opf_file not in content_files:
                content_files.append(opf_file)

            assert len(content_files) >= 4  # 2 chapters + toc.ncx + content.opf

            # 4. Create a mock translator that returns "[TRANS] text"
            config = Config.__new__(Config)
            config._config_path = "dummy"
            config._data = {
                "api_key": "test-key",
                "api_base": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "translation_mode": "bilingual",
                "max_retries": 1,
                "temperature": 0.3,
                "batch_size": 5,
            }

            mock_translator = Mock()
            mock_translator._config = config

            def mock_translate_batch(texts):
                return [f"[TRANS] {t}" for t in texts]

            mock_translator.translate_batch.side_effect = mock_translate_batch

            # 5. Parse and translate each file
            skip_tags = ["script", "style", "code", "pre"]
            all_texts = []
            all_fragments_map = {}
            parsed_files = []

            for file_path in content_files:
                parsed = parse_file(file_path, skip_tags)
                parsed_files.append(parsed)
                if parsed.fragments:
                    start_idx = len(all_texts)
                    for frag in parsed.fragments:
                        all_texts.append(frag.text)
                    all_fragments_map[len(parsed_files) - 1] = list(
                        range(start_idx, len(all_texts))
                    )

            assert len(all_texts) > 0, "Should have translatable text"

            # 6. Translate and write back
            translations = [None] * len(all_texts)
            uncached = [(i, t) for i, t in enumerate(all_texts) if cache.get(t) is None]

            if uncached:
                indices, texts = zip(*uncached)
                batch_translations = mock_translate_batch(list(texts))
                for idx, trans in zip(indices, batch_translations):
                    translations[idx] = trans
                    cache.put(all_texts[idx], trans)
                cache.flush()

            # Fill cached
            for i, text in enumerate(all_texts):
                if translations[i] is None:
                    translations[i] = cache.get(text)

            # Write back
            for file_idx, parsed in enumerate(parsed_files):
                if file_idx in all_fragments_map:
                    frag_indices = all_fragments_map[file_idx]
                    file_translations = [translations[i] for i in frag_indices]
                    parsed.save(file_translations)

            # 7. Rebuild EPUB
            output_path = rebuild_epub(
                extract_dir, output_dir, "sample"
            )
            assert os.path.exists(output_path)
            assert zipfile.is_zipfile(output_path)

            # 8. Verify the rebuilt EPUB
            with zipfile.ZipFile(output_path, "r") as zf:
                names = zf.namelist()
                assert names[0] == "mimetype"
                assert "OEBPS/chapter1.html" in names
                assert "OEBPS/chapter2.html" in names
                assert "OEBPS/images/test.png" in names

                # Read translated chapter to verify translations
                ch1_content = zf.read("OEBPS/chapter1.html").decode("utf-8")
                assert "[TRANS] Chapter 1 - Getting Started" in ch1_content
                assert "[TRANS] This is the first paragraph" in ch1_content
                assert "href=\"chapter2.html\"" in ch1_content  # links preserved
                assert "console.log" in ch1_content  # script preserved

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_parse_write_roundtrip(self):
        """Test that parsing, modifying, and saving preserves structure."""
        epub_path = "tests/fixtures/sample.epub"
        temp_dir = "temp/test_roundtrip"

        shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            extractor = EpubExtractor(epub_path, temp_dir)
            extractor.extract()

            content_files = extractor.list_content_files()
            assert len(content_files) >= 2

            for file_path in content_files:
                with open(file_path, "r", encoding="utf-8") as f:
                    original = f.read()

                parsed = parse_file(file_path, ["script", "style", "code", "pre"])
                if parsed.fragments:
                    translations = [f"[TR] {f.text}" for f in parsed.fragments]
                    parsed.save(translations)

                    with open(file_path, "r", encoding="utf-8") as f:
                        modified = f.read()

                    # Verify links are preserved
                    if "href" in original:
                        assert "href" in modified, "Link attributes must be preserved"

                    # Verify image tags are preserved
                    if "<img" in original:
                        assert "<img" in modified, "Image tags must be preserved"

                    # Verify script is preserved
                    if "script" in original.lower():
                        assert "script" in modified.lower()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_temp_contents_preserved(self):
        """Test that temp directory contents are preserved (not deleted) after extraction."""
        epub_path = "tests/fixtures/sample.epub"
        temp_dir = "temp/test_preserved"

        shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            extractor = EpubExtractor(epub_path, temp_dir)
            extractor.extract()

            # Verify files exist (extractor creates a book-name subdirectory)
            extract_dir = os.path.join(temp_dir, "sample")
            assert os.path.exists(os.path.join(extract_dir, "mimetype"))
            assert os.path.isdir(os.path.join(extract_dir, "OEBPS"))

            # Simulate running again (should use cache and not re-extract)
            extractor2 = EpubExtractor(epub_path, temp_dir)
            extractor2.extract()

            # Contents should still be there
            assert os.path.exists(os.path.join(extract_dir, "mimetype"))
            assert os.path.isdir(os.path.join(extract_dir, "OEBPS"))
            assert os.path.isdir(os.path.join(extract_dir, "META-INF"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
