"""Translate pages 1-4 of scanned PDF: Vision API OCR → translate → clean pages."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import fitz
from epub_translator.config import Config
from epub_translator.translator import Translator
from handlers.pdf_ocr import PdfOcr
from handlers.pdf_scanned_handler import PdfScannedRebuilder

BOOK = "How to Win Every Argument The Use and Abuse of Logic (Madsen Pirie) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")
OUTPUT = os.path.join(TEMP_DIR, "How_to_Win_Every_Argument_p1-4_ocr.pdf")

# 1. Extract pages 1-4
print("Extracting pages 1-4...")
doc = fitz.open(BOOK)
subset = fitz.open()
subset.insert_pdf(doc, from_page=0, to_page=3)
subset_path = os.path.join(TEMP_DIR, "_p1_4_subset.pdf")
subset.save(subset_path)
subset.close()
doc.close()
print(f"  {fitz.open(subset_path).page_count} pages saved")

# 2. Config
config = Config("config.yaml")
config.load()

# 3. Vision API OCR per page
print("OCR scanning with Vision API...")
ocr = PdfOcr(
    api_key=config.ocr_api_key,
    base_url=config.ocr_api_base or "https://api.siliconflow.com/v1",
    model=config.ocr_model or "Qwen/Qwen3-VL-32B-Instruct",
)
page_texts = ocr.ocr_pdf(subset_path, progress_callback=lambda d, t, txt=None: print(f"  OCR: {d}/{t} pages"))
for i, t in enumerate(page_texts):
    print(f"  Page {i+1}: {len(t)} chars — {t[:80]}...")

# 4. Translate
non_empty = [t for t in page_texts if t.strip()]
print(f"\nTranslating {len(non_empty)} pages...")
translator = Translator(config, target_lang="zh-CN", bilingual=False)
translations = translator.translate_all(non_empty)
for i, t in enumerate(translations):
    print(f"  Page {i+1}: {t[:100]}...")

# 5. Build clean bilingual PDF
print(f"\nBuilding clean pages to {OUTPUT}...")
sp = fitz.open(subset_path)
output = PdfScannedRebuilder._build_clean_text_pdf(
    page_texts=translations,
    page_sizes=[(sp[i].rect.width, sp[i].rect.height) for i in range(sp.page_count)],
    output_path=OUTPUT,
    bilingual=True,
    original_pdf=subset_path,
)
sp.close()
print(f"Done: {output}")
try:
    os.remove(subset_path)
except PermissionError:
    pass
