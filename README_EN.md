# EPUB Translator

A multi-format document translation tool based on Python + DeepSeek API. Supports EPUB, DOCX, and PDF, preserving original formatting and layout.

## Features

- **EPUB Translation**: Preserves formatting, images, links, and TOC. Bilingual/Chinese-only modes.
- **DOCX Translation**: Preserves paragraphs, tables, and formatting.
- **PDF Translation**: Three engines available (BabelDOC default / PDFMathTranslate / Native).
- **Web Interface**: Drag-and-drop upload, real-time progress, one-click download.
- **Translation Cache**: Incremental translation, resume after interruption.
- **Multi-language**: 30 target languages.
- **Cross-platform**: Windows / Linux / Mac.

## PDF Engine Comparison

| Engine | Approach | Layout Quality | Speed |
|--------|----------|---------------|-------|
| **BabelDOC** (default) | IL + document tree rebuild | ⭐⭐⭐⭐⭐ | Slower (loads model first time) |
| PDFMathTranslate | pdfminer content stream parsing | ⭐⭐⭐⭐ | Fast |
| Native | PyMuPDF exact bbox write-back | ⭐⭐⭐ | Fastest |

Switch engines freely in the Web UI after uploading a PDF.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt -r server/requirements.txt
# PDF enhancements (optional)
pip install BabelDOC                          # BabelDOC engine
uv tool install --python 3.12 pdf2zh          # PDFMathTranslate engine
```

### 2. Configure API Key

```bash
cp config.yaml.example config.yaml
# Edit config.yaml, add your DeepSeek API Key
```

### 3. Launch

**One-click launch (recommended):**

Windows: double-click `启动.bat`. Linux/Mac: `./启动.sh`.

**Manual launch:**

```bash
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

Open http://localhost:8080. Configure your API key in the Settings panel.

**CLI mode:**

```bash
python main.py path/to/book.epub
```

## Project Structure

```
├── main.py                          # CLI entry
├── config.yaml                      # Config (not committed)
├── 启动.bat / 启动.sh               # One-click launchers with auto-deploy
├── epub_translator/                 # Core library
│   ├── config.py                    # Config loading
│   ├── extractor.py                 # EPUB extraction
│   ├── parser.py                    # HTML/XHTML/NCX/OPF parsing
│   ├── translator.py                # DeepSeek API translation
│   ├── cache.py                     # Translation cache
│   └── rebuilder.py                 # EPUB rebuild
├── handlers/                        # Format adapters
│   ├── epub_handler.py              # EPUB translation
│   ├── docx_handler.py              # DOCX translation
│   ├── pdf_handler.py               # PDF (native + pdf2zh)
│   ├── pdf_babeldoc_handler.py      # PDF (BabelDOC)
│   └── PDFMathTranslate.md          # PDFMathTranslate integration docs
├── server/                          # Web UI
│   ├── server.py                    # FastAPI backend
│   └── index.html                   # Frontend
├── tests/                           # Test suite
├── docs/                            # Documentation
├── CLAUDE.md                        # Developer docs
└── README.md                        # This file (Chinese)
```

## Tests

```bash
pytest tests/ -v
```

## Configuration

| Item | Default | Description |
|------|---------|-------------|
| `translation_mode` | `bilingual` | `chinese_only` or `bilingual` |
| `batch_size` | 20 | Text segments per API call |
| `max_concurrency` | 5 | Concurrent API workers |
| `temperature` | 0.3 | Lower = more consistent translations |

## Supported APIs

Uses DeepSeek API by default. Compatible with any OpenAI-compatible API. Change `api_base` and `model` in `config.yaml`.

## License

MIT
