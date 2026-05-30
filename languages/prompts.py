"""Dynamic translation prompts based on target language."""

from .registry import LANGUAGES


def get_system_prompt(target_lang: str, bilingual: bool = False) -> str:
    """Generate a system prompt for the given target language.

    Args:
        target_lang: Target language code (e.g. 'zh-CN', 'ja', 'fr')
        bilingual: If True, instruct to preserve original structure for bilingual output
    """
    lang_entry = LANGUAGES.get(target_lang, {})
    lang_name = lang_entry.get("name_en", target_lang)

    rules = [
        f"You are a professional translator. Translate the following text into {lang_name}.",
        "For each text segment separated by the delimiter '|||', return only the translation.",
        "",
        "Important rules:",
        "1. Preserve all HTML tags and entities exactly as they appear",
        "2. Do not translate proper nouns that should stay in original (brand names, URLs, code)",
        "3. Preserve numbers, dates, and punctuation structure",
        "4. Return each translation segment separated by '|||' in the same order",
        "5. Do not add any extra commentary or explanation",
        "6. For source code: keep all code and syntax completely unchanged, translate ONLY the comments within the code",
    ]

    if bilingual:
        rules.append(
            f"7. This is bilingual mode — the translation will appear alongside the original text, "
            f"so ensure the {lang_name} translation is natural and readable on its own"
        )

    return "\n".join(rules)
