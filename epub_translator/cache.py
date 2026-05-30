import hashlib
import json
import os


class TranslationCache:
    def __init__(self, cache_dir: str):
        self._cache_path = os.path.join(cache_dir, "translation_cache.json")
        self._data: dict[str, str] = {}
        self._dirty = False

    def load(self):
        if os.path.exists(self._cache_path):
            with open(self._cache_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    def save(self):
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.strip().encode("utf-8")).hexdigest()

    def get(self, text: str) -> str | None:
        key = self._hash(text)
        return self._data.get(key)

    def put(self, original: str, translated: str):
        key = self._hash(original)
        if self._data.get(key) != translated:
            self._data[key] = translated
            self._dirty = True

    def batch_get(self, texts: list[str]) -> tuple[list[str], list[int]]:
        """Returns (untranslated_texts, untranslated_indices)."""
        untranslated = []
        untranslated_indices = []
        for i, text in enumerate(texts):
            cached = self.get(text)
            if cached is not None:
                continue
            untranslated.append(text)
            untranslated_indices.append(i)
        return untranslated, untranslated_indices

    def flush(self):
        if self._dirty:
            self.save()
            self._dirty = False

    def clear(self):
        self._data.clear()
        if os.path.exists(self._cache_path):
            os.remove(self._cache_path)
