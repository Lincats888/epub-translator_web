"""Source language detection with caching for performance.

Uses langdetect library. Results are cached to avoid redundant detection
for documents with many similar fragments.
"""

import hashlib
from functools import lru_cache

from .registry import LANGUAGES


@lru_cache(maxsize=256)
def detect_language(text: str) -> str:
    """Detect the language of a text string.

    Args:
        text: Input text (at least a few words for accuracy)

    Returns:
        Language code like 'en', 'zh-cn', 'ja', etc.
        Returns 'unknown' if detection fails.
    """
    if not text or len(text.strip()) < 10:
        return "unknown"

    try:
        from langdetect import detect
        raw = detect(text)
        return _normalize_code(raw)
    except Exception:
        return "unknown"


def _normalize_code(detected: str) -> str:
    """Normalize langdetect output to our registry codes.

    langdetect returns: 'en', 'zh-cn', 'zh-tw', 'ja', 'ko', etc.
    Our registry uses the same codes, so this is mostly passthrough.
    """
    detected = detected.lower().strip()
    # langdetect returns 'zh-cn'/'zh-tw' for Chinese
    if detected in ("zh-cn", "zh-cn", "zh"):
        return "zh-CN"
    if detected == "zh-tw":
        return "zh-TW"
    return detected


def is_same_language(text: str, target_lang: str) -> bool:
    """Check if text is already in the target language.

    For batch efficiency, only samples the first fragment to decide.
    """
    source = detect_language(text)
    if source == "unknown":
        return False

    # Normalize target for comparison
    target_norm = target_lang.lower()
    source_norm = source.lower()

    # Handle Chinese variants
    if target_norm.startswith("zh") and source_norm.startswith("zh"):
        return True

    return source_norm == target_norm
