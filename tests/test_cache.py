import json
import os
import tempfile
import pytest

from epub_translator.cache import TranslationCache


class TestTranslationCache:
    @pytest.fixture
    def cache_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_hash_consistency(self):
        h1 = TranslationCache._hash("Hello World")
        h2 = TranslationCache._hash("Hello World")
        h3 = TranslationCache._hash("hello world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 32  # MD5 hex length

    def test_put_and_get(self, cache_dir):
        cache = TranslationCache(cache_dir)
        cache.put("Hello", "你好")
        assert cache.get("Hello") == "你好"
        assert cache.get("Not cached") is None

    def test_save_and_load(self, cache_dir):
        cache1 = TranslationCache(cache_dir)
        cache1.put("Hello", "你好")
        cache1.save()

        cache2 = TranslationCache(cache_dir)
        cache2.load()
        assert cache2.get("Hello") == "你好"

    def test_batch_get(self, cache_dir):
        cache = TranslationCache(cache_dir)
        cache.put("one", "一")
        cache.put("three", "三")

        texts = ["one", "two", "three", "four"]
        untranslated, indices = cache.batch_get(texts)
        assert untranslated == ["two", "four"]
        assert indices == [1, 3]

    def test_clear(self, cache_dir):
        cache = TranslationCache(cache_dir)
        cache.put("Hello", "你好")
        cache.save()
        assert os.path.exists(cache._cache_path)
        cache.clear()
        assert not os.path.exists(cache._cache_path)
        assert cache._data == {}

    def test_flush_only_when_dirty(self, cache_dir):
        cache = TranslationCache(cache_dir)
        assert not os.path.exists(cache._cache_path)
        cache.flush()  # not dirty, should not create file
        assert not os.path.exists(cache._cache_path)
        cache.put("test", "测试")
        cache.flush()
        assert os.path.exists(cache._cache_path)
