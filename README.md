# EPUB Translator

基于 Python + DeepSeek API 的多格式文档翻译工具。支持 EPUB、DOCX、PDF，保留原始格式和布局。

## 功能特性

- **EPUB 翻译**：保留格式、图片、链接和目录结构，支持双语/纯中文
- **DOCX 翻译**：保留段落、表格和格式
- **PDF 翻译**：三引擎可选（BabelDOC 默认 / PDFMathTranslate / 原生）
- **Web 界面**：拖拽上传，实时进度，一键下载
- **翻译缓存**：支持增量翻译，中断后可继续
- **多语种**：30 种目标语言可选
- **跨平台**：Windows / Linux / Mac

## PDF 引擎对比

| 引擎 | 原理 | 排版 | 速度 |
|------|------|------|------|
| **BabelDOC**（默认）| IL 中间表示 + 文档树重建 | ⭐⭐⭐⭐⭐ | 较慢（首次加载模型） |
| PDFMathTranslate | pdfminer 内容流解析 | ⭐⭐⭐⭐ | 快 |
| 原生 | PyMuPDF exact bbox 写回 | ⭐⭐⭐ | 最快 |

Web 界面中上传 PDF 后可自由切换引擎。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt -r server/requirements.txt
# PDF 增强（可选）
pip install BabelDOC               # BabelDOC 引擎
uv tool install --python 3.12 pdf2zh   # PDFMathTranslate 引擎
```

### 2. 配置 API Key

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入你的 DeepSeek API Key
```

### 3. 启动

**一键启动（推荐）：**

Windows 双击 `启动.bat`，Linux/Mac 运行 `./启动.sh`。

**手动启动：**

```bash
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

浏览器打开 http://localhost:8080，可在设置面板中配置 API Key。

**CLI 模式：**

```bash
python main.py path/to/book.epub
```

## 项目结构

```
├── main.py                          # CLI 入口
├── config.yaml                      # 配置文件（不提交）
├── 启动.bat / 启动.sh               # 一键启动（含自动部署）
├── epub_translator/                 # 核心库
│   ├── config.py                    # 配置加载
│   ├── extractor.py                 # EPUB 解压
│   ├── parser.py                    # HTML/XHTML/NCX/OPF 解析
│   ├── translator.py                # DeepSeek API 翻译
│   ├── cache.py                     # 翻译缓存
│   └── rebuilder.py                 # EPUB 重建
├── handlers/                        # 文件格式适配器
│   ├── epub_handler.py              # EPUB 翻译
│   ├── docx_handler.py              # DOCX 翻译
│   ├── pdf_handler.py               # PDF 翻译（原生 + pdf2zh）
│   ├── pdf_babeldoc_handler.py      # PDF 翻译（BabelDOC）
│   └── PDFMathTranslate.md          # PDFMathTranslate 集成文档
├── server/                          # Web 界面
│   ├── server.py                    # FastAPI 后端
│   └── index.html                   # 前端页面
├── tests/                           # 测试套件
├── docs/                            # 文档
│   └── PDF-DOM研究提纲.md           # PDF 内容流研究
├── CLAUDE.md                        # 项目开发文档
└── README.md                        # 本文件
```

## 测试

```bash
pytest tests/ -v
```

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `translation_mode` | `bilingual` | `chinese_only` 或 `bilingual` |
| `batch_size` | 20 | 每次 API 调用翻译的段落数 |
| `max_concurrency` | 5 | 并发 API 工作线程数 |
| `temperature` | 0.3 | 翻译一致性（低值 = 更一致） |

## 支持的 API

默认使用 DeepSeek API，也兼容其他 OpenAI 兼容的 API。在 `config.yaml` 中修改 `api_base` 和 `model` 即可。

## License

MIT
