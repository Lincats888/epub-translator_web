"""Multi-language support for EPUB Translator.

Provides language registry, source language detection, and dynamic
translation prompts for any target language.
"""

from .registry import LANGUAGES, get_lang_name, get_all_languages
from .detector import detect_language, is_same_language
from .prompts import get_system_prompt
