import pytest
from unittest.mock import Mock, patch, MagicMock

from epub_translator.config import Config
from epub_translator.translator import Translator, TRANSLATION_DELIMITER


class TestTranslator:
    @pytest.fixture
    def config_chinese_only(self):
        c = Config.__new__(Config)
        c._config_path = "dummy"
        c._data = {
            "api_key": "test-key",
            "api_base": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "translation_mode": "chinese_only",
            "max_retries": 2,
            "temperature": 0.3,
        }
        return c

    @pytest.fixture
    def config_bilingual(self):
        c = Config.__new__(Config)
        c._config_path = "dummy"
        c._data = {
            "api_key": "test-key",
            "api_base": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "translation_mode": "bilingual",
            "max_retries": 2,
            "temperature": 0.3,
        }
        return c

    def test_translate_batch_returns_correct_count(self, config_chinese_only):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=f"你好{TRANSLATION_DELIMITER}世界{TRANSLATION_DELIMITER}测试"
                )
            )
        ]

        with patch.object(Translator, "__init__", lambda self, c: None):
            translator = Translator.__new__(Translator)
            translator._config = config_chinese_only
            translator._client = Mock()
            translator._client.chat.completions.create.return_value = mock_response

            texts = ["Hello", "World", "Test"]
            results = translator.translate_batch(texts)

            assert len(results) == 3
            assert results[0] == "你好"
            assert results[1] == "世界"
            assert results[2] == "测试"

    def test_translate_empty_batch(self, config_chinese_only):
        with patch.object(Translator, "__init__", lambda self, c: None):
            translator = Translator.__new__(Translator)
            translator._config = config_chinese_only
            assert translator.translate_batch([]) == []

    def test_translate_retry_on_failure(self, config_chinese_only):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="你好"))
        ]

        with patch.object(Translator, "__init__", lambda self, c: None):
            translator = Translator.__new__(Translator)
            translator._config = config_chinese_only
            translator._client = Mock()
            translator._client.chat.completions.create.side_effect = [
                Exception("Network error"),
                mock_response,
            ]

            results = translator.translate_batch(["Hello"])
            assert len(results) == 1
            assert results[0] == "你好"
            assert translator._client.chat.completions.create.call_count == 2

    def test_chinese_only_prompt(self, config_chinese_only):
        with patch.object(Translator, "__init__", lambda self, c: None):
            translator = Translator.__new__(Translator)
            translator._config = config_chinese_only
            prompt = translator._get_system_prompt()
            assert "only the chinese translation" in prompt.lower()

    def test_bilingual_prompt(self, config_bilingual):
        with patch.object(Translator, "__init__", lambda self, c: None):
            translator = Translator.__new__(Translator)
            translator._config = config_bilingual
            prompt = translator._get_system_prompt()
            assert "simplified chinese" in prompt.lower()
