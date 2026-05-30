"""Language registry — maps language codes to display names."""

# Core languages (most commonly used)
LANGUAGES = {
    "zh-CN": {"name_zh": "简体中文",     "name_en": "Simplified Chinese", "detect_code": "zh-cn"},
    "zh-TW": {"name_zh": "繁體中文",     "name_en": "Traditional Chinese", "detect_code": "zh-tw"},
    "en":    {"name_zh": "英语",         "name_en": "English", "detect_code": "en"},
    "ja":    {"name_zh": "日语",         "name_en": "Japanese", "detect_code": "ja"},
    "ko":    {"name_zh": "韩语",         "name_en": "Korean", "detect_code": "ko"},
    "fr":    {"name_zh": "法语",         "name_en": "French", "detect_code": "fr"},
    "de":    {"name_zh": "德语",         "name_en": "German", "detect_code": "de"},
    "es":    {"name_zh": "西班牙语",     "name_en": "Spanish", "detect_code": "es"},
    "pt":    {"name_zh": "葡萄牙语",     "name_en": "Portuguese", "detect_code": "pt"},
    "ru":    {"name_zh": "俄语",         "name_en": "Russian", "detect_code": "ru"},
    "ar":    {"name_zh": "阿拉伯语",     "name_en": "Arabic", "detect_code": "ar"},
    "it":    {"name_zh": "意大利语",     "name_en": "Italian", "detect_code": "it"},
    "nl":    {"name_zh": "荷兰语",       "name_en": "Dutch", "detect_code": "nl"},
    "pl":    {"name_zh": "波兰语",       "name_en": "Polish", "detect_code": "pl"},
    "tr":    {"name_zh": "土耳其语",     "name_en": "Turkish", "detect_code": "tr"},
    "vi":    {"name_zh": "越南语",       "name_en": "Vietnamese", "detect_code": "vi"},
    "th":    {"name_zh": "泰语",         "name_en": "Thai", "detect_code": "th"},
    "id":    {"name_zh": "印尼语",       "name_en": "Indonesian", "detect_code": "id"},
    "ms":    {"name_zh": "马来语",       "name_en": "Malay", "detect_code": "ms"},
    "hi":    {"name_zh": "印地语",       "name_en": "Hindi", "detect_code": "hi"},
    "uk":    {"name_zh": "乌克兰语",     "name_en": "Ukrainian", "detect_code": "uk"},
    "cs":    {"name_zh": "捷克语",       "name_en": "Czech", "detect_code": "cs"},
    "sv":    {"name_zh": "瑞典语",       "name_en": "Swedish", "detect_code": "sv"},
    "da":    {"name_zh": "丹麦语",       "name_en": "Danish", "detect_code": "da"},
    "fi":    {"name_zh": "芬兰语",       "name_en": "Finnish", "detect_code": "fi"},
    "no":    {"name_zh": "挪威语",       "name_en": "Norwegian", "detect_code": "no"},
    "el":    {"name_zh": "希腊语",       "name_en": "Greek", "detect_code": "el"},
    "he":    {"name_zh": "希伯来语",     "name_en": "Hebrew", "detect_code": "he"},
    "ro":    {"name_zh": "罗马尼亚语",   "name_en": "Romanian", "detect_code": "ro"},
    "hu":    {"name_zh": "匈牙利语",     "name_en": "Hungarian", "detect_code": "hu"},
}


def get_lang_name(lang_code: str, ui_lang: str = "zh") -> str:
    """Get display name for a language code.

    Args:
        lang_code: e.g. 'zh-CN', 'en', 'ja'
        ui_lang: 'zh' or 'en' — the UI language

    Returns:
        Display name in the appropriate language
    """
    entry = LANGUAGES.get(lang_code)
    if not entry:
        return lang_code
    return entry["name_zh"] if ui_lang == "zh" else entry["name_en"]


def get_all_languages(ui_lang: str = "zh") -> list[dict]:
    """Get all languages as a list of {code, name} for dropdown menus.

    Args:
        ui_lang: 'zh' or 'en'

    Returns:
        List of {"code": "zh-CN", "name": "简体中文"} sorted by name
    """
    result = []
    for code, entry in LANGUAGES.items():
        name = entry["name_zh"] if ui_lang == "zh" else entry["name_en"]
        result.append({"code": code, "name": name})
    # Sort by name, but keep zh-CN first
    result.sort(key=lambda x: (x["code"] != "zh-CN", x["name"]))
    return result
