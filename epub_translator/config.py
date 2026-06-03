import os
import yaml

from epub_translator.crypto import decrypt, is_encrypted

DEFAULT_CONFIG = {
    "api_key": "",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "translation_mode": "bilingual",
    "source_language": "English",
    "target_language": "Simplified Chinese",
    "batch_size": 20,
    "max_concurrency": 5,
    "max_retries": 3,
    "api_timeout": 120,
    "temperature": 0.3,
    "skip_tags": ["script", "style", "code", "pre"],
}


class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self._config_path = config_path
        self._data = dict(DEFAULT_CONFIG)

    def load(self):
        if not os.path.exists(self._config_path):
            self._create_config()
        with open(self._config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._data:
                self._data[key] = value

    def _create_config(self):
        """Create config.yaml from example file or defaults."""
        example_path = self._config_path + ".example"
        if os.path.exists(example_path):
            with open(example_path, "r", encoding="utf-8") as f:
                content = f.read()
            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, allow_unicode=True)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    @property
    def api_key(self) -> str:
        raw = self._data.get("api_key", "")
        if is_encrypted(raw):
            return decrypt(raw)
        return raw

    @property
    def api_base(self) -> str:
        val = self._data.get("api_base", "")
        return val if val else "https://api.deepseek.com"

    @property
    def model(self) -> str:
        val = self._data.get("model", "")
        return val if val else "deepseek-chat"

    @property
    def translation_mode(self) -> str:
        return self._data.get("translation_mode", "bilingual")

    @property
    def source_language(self) -> str:
        return self._data.get("source_language", "English")

    @property
    def target_language(self) -> str:
        return self._data.get("target_language", "Simplified Chinese")

    @property
    def batch_size(self) -> int:
        return self._data.get("batch_size", 5)

    @property
    def max_retries(self) -> int:
        return self._data.get("max_retries", 3)

    @property
    def temperature(self) -> float:
        return self._data.get("temperature", 0.3)

    @property
    def max_concurrency(self) -> int:
        return self._data.get("max_concurrency", 5)

    @property
    def api_timeout(self) -> int:
        return self._data.get("api_timeout", 120)

    @property
    def skip_tags(self) -> list:
        return self._data.get("skip_tags", ["script", "style", "code", "pre"])
