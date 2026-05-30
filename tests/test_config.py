import os
import shutil
import pytest

from epub_translator.config import Config


class TestConfig:
    @pytest.fixture
    def config(self):
        c = Config("config.yaml")
        c.load()
        return c

    def test_load_defaults(self):
        c = Config("nonexistent.yaml")
        c.load()
        assert c.translation_mode == "bilingual"
        assert c.batch_size == 20
        assert c.max_concurrency == 5
        assert c.api_timeout == 120
        assert c.max_retries == 3
        assert c.temperature == 0.3
        assert "script" in c.skip_tags

    def test_get_method(self, config):
        assert config.get("batch_size") == 20
        assert config.get("nonexistent", "default") == "default"

    def test_properties(self, config):
        assert isinstance(config.api_key, str)
        assert isinstance(config.api_base, str)
        assert isinstance(config.model, str)
        assert config.translation_mode in ("bilingual", "chinese_only")
        assert isinstance(config.skip_tags, list)
