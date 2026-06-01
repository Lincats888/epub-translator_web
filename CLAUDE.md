# CLAUDE.md

本文件为 Claude Code 在此代码库中的工作提供指引。

## 项目概述

EPUB Translator —— 基于 Python + DeepSeek API 的 EPUB 电子书翻译工具。将英文 EPUB 翻译为中文（或中英双语），保留原始格式、图片、链接和目录结构。提供两种使用方式：

- **CLI 命令行**：`python main.py <epub文件>`
- **Web 界面**：双击 `启动.bat`，浏览器打开 http://localhost:8080

## 快速开始

```bash
# CLI 模式
python main.py path/to/book.epub

# Web 模式（Windows）
双击 启动.bat

# Web 模式（手动）
pip install -r requirements.txt -r server/requirements.txt
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

## 测试

```bash
pytest tests/ -v          # 全部测试
pytest tests/ --cov=epub_translator  # 带覆盖率
```

## 项目架构

```
main.py                          # CLI 入口，编排整个翻译流程
epub_translator/                 # 核心库（不应直接修改）
  config.py                      # YAML 配置加载，Config 类（自动解密 api_key）
  crypto.py                      # Fernet AES 加密：API Key 加密存储
  extractor.py                   # EpubExtractor：解压 EPUB 到 temp/，基于 MD5 哈希跳过重复解压
  parser.py                      # HTML/XHTML/NCX/OPF 解析 → ParsedFile + TextFragment 列表
  translator.py                  # Translator：通过 OpenAI SDK 调用 DeepSeek API，并发批量翻译
  cache.py                       # TranslationCache：MD5 哈希 → 翻译 JSON 缓存，支持增量运行
  rebuilder.py                   # 重建 EPUB：mimetype 优先 + STORED，其他文件 DEFLATED
handlers/                        # 文件格式适配器（BaseHandler 抽象基类）
  epub_handler.py                # EPUB：封装现有 epub_translator
  docx_handler.py                # DOCX：python-docx，保留段落/表格/格式
  pdf_handler.py                 # PDF：PyMuPDF，双语输出为交替页面
languages/                       # 多语种支持
  registry.py                    # 30 种语言注册表
  detector.py                    # 源语言检测（langdetect），同语种跳过
  prompts.py                     # 按目标语言动态生成翻译提示词
server/                          # Web 界面（FastAPI + SSE）
  server.py                      # 后端：多格式分发、翻译控制、SSE 进度、语言 API
  index.html                     # 前端：极简 2.0 风格，中英双语切换
  guide.html                     # 使用指南页面（含截图）
```

## Handler 架构

所有文件格式通过 `BaseHandler` 抽象基类统一处理：

```python
class BaseHandler(ABC):
    def supported_extensions() -> list[str]  # 如 ['.epub']
    def extract(file_path) -> list[TextFragment]  # 提取可翻译片段
    def rebuild(file_path, fragments, translations, bilingual, target_lang) -> str  # 回写
```

- **注册机制**：`handlers/__init__.py` 自动扫描所有 Handler，按扩展名分发
- **新增格式**：只需新建 Handler 类并在 `__init__.py` 注册，不影响现有代码
- **EPUB 特殊处理**：不走通用翻译流程，直接在 `server.py` 中调用原有 `_run_translate`

## 多语种支持

- **30 种语言**（`languages/registry.py`）：中/英/日/韩/法/德/西/俄/阿...
- **源语言检测**（`langdetect`）：检测原文语种，若与目标语种相同则跳过翻译
- **动态提示词**：根据目标语言生成翻译 system prompt
- **API**：`GET /api/languages?ui=zh|en` 返回语言列表
- **前端**：下拉框选择目标语言 + 双语/目标语言模式

## PDF 翻译细节

PDF 翻译提供**双引擎**，用户可在 Web 界面下拉框中选择：

### 引擎 1: PDFMathTranslate（推荐，默认）
- 集成开源项目 [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)
- 使用 pdfminer.six 解析 PDF 内容流 → 翻译 → 重建，排版效果好
- 通过 `PdfHandler.rebuild_via_pdf2zh()` 调用，自动查找系统中的 pdf2zh 二进制
- 详见 `handlers/PDFMathTranslate.md`
- **安装**：`uv tool install --python 3.12 pdf2zh`（需要 Python 3.10-3.12）
- **配置**：独立的 `~/.config/PDFMathTranslate/config.json`，不读 `config.yaml`

### 引擎 2: 原生 PyMuPDF（备选）
- 提取 → 翻译 → exact bbox 回写（宽高不变，字号自适应收缩）
- 代码检测：仅多行（3+ 行）且 >30% 匹配才跳过，单行不判代码
- 同块异色/异位 span 自动拆分（颜色不同或 x 间距 >80px）
- ALL-CAPS 短词（≥2 字符）不会被过滤

### 文字提取
- 使用 PyMuPDF (fitz)，逐页逐行提取，每行独立记录位置（bbox）、字号、颜色、行方向（旋转）
- 检测扫描版 PDF（>70% 页面无文字）→ 直接报错
- 同块 span 拆分：颜色不同 → 独立片段；x 间距 >80px → 独立片段（如 `[标题]______[CHAPTER 3]`）

### 中文渲染
- CJK 字体：SimSun 宋体优先，SimHei 黑体备选
- 颜色保留：`TextWriter(color=color_rgb)` 逐块设置
- 粗体/斜体：`_map_font()` 映射到 `hebo`/`heit`/`hebi`
- 旋转文字：`insert_textbox(morph=(point, matrix))` 保留原始倾角（15° 等任意角度）
- 字号：`_optimal_font_size()` 二分查找最大能装下的字号，最小不低于原字号的 70%

### 双语模式
- 输出为交替页面：Page 0=英文，Page 1=中文，Page 2=英文...
- 全量翻译后在单独副本上修改，再 `insert_pdf()` + `move_page()` 重排

### 排版
- **exact bbox 策略**：每个文字块用原文精确矩形写回，宽高不变，字体缩到能装下
- 核心理念：接受缩字，换布局零偏移
- 不再使用流式排版或列感知排版（已回退，效果不佳）

## API Key 加密

- API Key 使用 Fernet（AES-128-CBC + HMAC）加密后存入 `config.yaml`
- 加密密钥存储在 `.secret` 文件（已 gitignore）
- `Config.api_key` 属性自动解密
- Web 设置页面显示绿色锁图标和"已加密存储"标签

## 翻译流水线

1. `Config.load()` — 读取 `config.yaml`，合并默认值
2. `EpubExtractor.extract()` — 解压 EPUB 到 `temp/{book_name}/`，源文件哈希匹配则跳过
3. `parse_file()` 逐文件解析 — BeautifulSoup 提取 `TextFragment`（跳过 script/style/code/pre）
4. `TranslationCache` — 检查 MD5 缓存，收集未缓存的文本
5. `Translator.translate_all()` — 并发批量请求（ThreadPoolExecutor，分隔符 `|||`）
6. `ParsedFile.save()` — 双语模式：克隆元素插入翻译；中文模式：原地替换文本
7. `rebuild_epub()` — 打包为 EPUB（mimetype 首位 + STORED），输出到 `output/{book_name}_zh.epub`

## 关键约束

- **mimetype 必须是 ZIP 中第一个文件且不压缩**（EPUB 规范，由 `rebuilder.py` 的排序逻辑处理）
- **不要使用 `soup.prettify()`** — 会改变 HTML 空白。直接用 `str(soup)`
- **XHTML 文件使用 XML 命名空间** — `.xhtml`/`.xml` 文件或检测到 `xmlns` 时使用 `BeautifulSoup(content, "xml")`
- **表格双语处理** — 每个单元格作为独立片段翻译，翻译文本直接写入原单元格（`<br/>` 分隔英文和中文），不创建额外的表格或 HTML 元素
- **代码块不翻译** — `<pre>`/`<code>` 标签及 CSS 类名 `class_sch`/`class_skus`/`class_scn` 标记的代码段均跳过
- **内联格式保留** — 双语模式使用 `decode_contents()` 提取 HTML，API 返回保留 `<strong>`/`<a>`/`<em>` 等标签的翻译
- **`lang` 属性标记** — EPUB 翻译时为每个元素添加 `lang` 属性：原文 `lang="en"`，译文 `lang="zh-CN"`（或其他目标语种），方便阅读器通过 CSS `:lang()` 伪类区分样式
- **其他文件类型仅保留格式** — DOCX、PDF 等非 HTML 文件类型的翻译只需保留原始格式（加粗、斜体、字号等），无须添加任何额外标记（如 `lang` 属性）

## 配置说明

`config.yaml` 为唯一配置文件（不提交，包含 API 密钥）。`Config` 类提供所有默认值：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `translation_mode` | `bilingual` | `chinese_only` 或 `bilingual` |
| `batch_size` | 20 | 每次 API 调用翻译的段落数 |
| `max_concurrency` | 5 | 并发 API 工作线程数 |
| `temperature` | 0.3 | 低值 = 翻译更一致 |
| `skip_tags` | script, style, code, pre | 不翻译其中的内容 |

Web 界面也可通过设置弹窗修改配置（齿轮图标 → `POST /api/config`）。

## 解析和回写细节

### 双语模式（bilingual）

- **普通段落/标题**：克隆元素，插入原元素之后。克隆包含翻译文本
- **表格单元格**：每个 `<td>`/`<th>` 作为独立片段，翻译文本用 `<br/>` 追加到原单元格内
- **代码块**：跳过不翻译
- **`data-epub-translator` 标记**：添加到已处理元素，重新解析时跳过，保证幂等性
- **`lang` 属性**：原文元素添加 `lang="en"`，译文克隆添加 `lang="zh-CN"`（或其他目标语种）。输出示例：
  ```html
  <p lang="en" data-epub-translator="1">English paragraph</p>
  <p lang="zh-CN" data-epub-translator="1">中文段落</p>
  ```

### 中文模式（chinese_only）

- 直接替换 `NavigableString` 内容为翻译文本

### 非 HTML 文件类型（DOCX 等）

- 仅保留原始格式（加粗、斜体、字号、颜色等），不添加 `lang` 或其他标记
- 双语模式：在原文段落/单元格下方插入译文段落/单元格
- 替换模式：直接替换原文内容为译文

## Web 服务器 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 前端页面 |
| `/guide` | GET | 使用指南页面 |
| `/api/upload` | POST | 上传文件（.epub/.docx/.pdf），返回元数据 |
| `/api/start/{id}` | POST | 开始翻译（body: target_lang, bilingual, pdf_method） |
| `/api/stop/{id}` | POST | 停止翻译 |
| `/api/progress/{id}` | GET | SSE 流，实时推送翻译进度 |
| `/api/download/{id}` | GET | 下载翻译完成的文件 |
| `/api/languages?ui=` | GET | 获取语种列表 |
| `/api/formats` | GET | 获取支持的文件格式 |
| `/api/config` | GET/POST | 读取/更新配置（含加密 Key） |

## 目录结构约定

| 路径 | 用途 |
|------|------|
| `temp/` | 解压的 EPUB 内容（运行时保留，支持增量翻译） |
| `temp/_uploads/` | Web 界面上传的 EPUB 文件 |
| `temp/{book_name}/translation_cache.json` | 翻译缓存（每本书独立） |
| `output/` | 翻译完成的 EPUB 输出（`*_zh.epub`） |
| `tests/fixtures/sample.epub` | 最小测试 EPUB（含段落、表格、代码、图片） |

## 跨平台部署

- **Windows**: 双击 `启动.bat`，自动安装依赖 + PDFMathTranslate + 启动服务
- **Linux/Mac**: `chmod +x 启动.sh && ./启动.sh`

## 依赖

- Python 3.10+
- 核心：beautifulsoup4, lxml, openai, pyyaml, tqdm, cryptography, langdetect
- Web 服务：fastapi, uvicorn, python-multipart
- 格式处理：python-docx, PyMuPDF
- PDF 增强（可选）：pdf2zh (PDFMathTranslate, 需 Python 3.10-3.12)
