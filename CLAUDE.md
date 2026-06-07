# CLAUDE.md

本文件为 Claude Code 在此代码库中的工作提供指引。

## 项目概述

EPUB Translator —— 基于 Python + DeepSeek API 的多格式文档翻译工具。支持 EPUB、DOCX、PDF（文字型 + 扫描版），提供 CLI 命令行和 Web 界面两种使用方式。

- **CLI 命令行**：`python main.py <epub文件>`
- **Web 界面**：双击 `启动.bat`，浏览器打开 http://localhost:8080

## 快速开始

```bash
pip install -r requirements.txt
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

## 项目架构

```
main.py                          # CLI 入口
epub_translator/                 # 核心库
  config.py                      # YAML 配置 + 默认值（api_key 自动解密）
  crypto.py                      # Fernet AES 加密
  extractor.py                   # EpubExtractor：解压 + MD5 哈希跳过重复
  parser.py                      # HTML/XHTML 解析 → TextFragment
  translator.py                  # Translator：OpenAI SDK, ThreadPoolExecutor 并发
  cache.py                       # TranslationCache：MD5 → JSON 增量缓存
  rebuilder.py                   # 重建 EPUB
handlers/                        # 格式适配器（BaseHandler 抽象基类 + **kwargs）
  base.py                        # 抽象基类（extract/rebuild 均支持 **kwargs）
  epub_handler.py                # EPUB
  docx_handler.py                # DOCX：python-docx
  pdf_handler.py                 # PDF：PyMuPDF + pdf2zh CLI 集成 + 页码过滤
  pdf_babeldoc_handler.py        # PDF：BabelDOC v0.6.2 IL 管道（去水印）
  pdf_ocr.py                     # 扫描版 PDF：Vision API OCR（SiliconFlow/Qwen3-VL）
  pdf_scanned_handler.py         # 扫描版 PDF 重建：Surya/EasyOCR + OpenCV + clean page
languages/                       # 30 种语言
server/                          # Web 界面（FastAPI + SSE）
  server.py                      # 后端：多格式分发、SSE 进度、翻译历史 API
  settings-modal.js              # 共享设置弹窗（所有页面引用）
  history_store.py               # SQLite 翻译历史（userid='epubTranslator'）
  translation_history.db         # 历史数据库（已提交）
  index.html                     # 首页（内联设置弹窗）
  translate.html                 # 单文件翻译页（EPUB/DOCX/PDF）
  batch.html                     # 批量翻译页（日期查询、独立行设置、文件名搜索）
  ocrtranslate.html              # OCR 翻译页（扫描版 PDF）
  guide.html                     # 使用指南
```

## Handler 架构

```python
class BaseHandler(ABC):
    def supported_extensions() -> list[str]
    def extract(file_path, skip_tags=None, bilingual=True, **kwargs) -> list[TextFragment]
    def rebuild(file_path, fragments, translations, bilingual, target_lang="zh-CN", **kwargs) -> str
```

- `**kwargs` 允许传递引擎特定参数（如 PDF 的 `pages`、`method`），非 PDF handler 静默忽略
- EPUB 特殊处理：不走通用流程，`server.py` 直接调用 `_run_translate()`

## PDF 三引擎

| 引擎 | 下拉选项 | 适用场景 | 页码选择 | 竖向文本 |
|------|----------|----------|----------|----------|
| BabelDOC | 精确图文排版 | 文字型 PDF，最佳排版 | ✅ `pages="1-5,8"` | ❌ 硬编码跳过 |
| PDFMathTranslate | 数学公式排版 | 含公式 PDF、竖排 | ✅ `--pages` CLI | ✅ |
| Native PyMuPDF | 普通排版 | 简单 PDF | ✅ extract 过滤 | ✅ |

## OCR 翻译流水线（扫描版 PDF）

```
扫描检测(>70%页面无文字) → Vision API OCR(Qwen3-VL, 150DPI)
  → 翻译(DeepSeek) → Clean Page 重建(Surya检测 + PyMuPDF TextWriter)
```
- OCR 使用 SiliconFlow Vision API，逐页发送 PNG → 返回文字
- Surya DetectionPredictor 做行级检测，OpenCV inpaint 擦除原文
- 输出为洁净文字页（统一字体排版）+ 可选双语交替页

## EPUB 翻译优化

- **全局合批**：预扫描所有文件 → 收集全部未缓存片段 → 一次性 `translate_all()`
- 500 片段 ÷ 20 一批 × 5 并发 ≈ 5 轮 API 调用（之前逐文件需 ~45 次串行）
- 进度条精确显示累积片段进度 `324/780 fragments` + 当前文件名

## 翻译历史数据库

- SQLite：`server/translation_history.db`，表 `translation_history`
- `userid` 字段默认 `"epubTranslator"`（单用户版 hardcode，多用户版预留）
- 同一 userid+filename 只保留一条记录（upsert），再次翻译更新原记录
- 翻译完成自动记录，翻译失败/停止不记为 done
- API：`/api/history/check` `/tasks` `/search` `/dates`

## 重复翻译检测

- 开始翻译前 `GET /api/history/check?filename=xxx`
- 已翻译成功 → 后端返回 409 → 前端弹确认框"已于 YYYY-MM-DD 翻译完成，重新翻译？"
- 确认后带 `force=true` 重新发起
- 只对 `status='done'` 的记录弹窗

## 配置

`config.yaml` 完整配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `api_key` | — | DeepSeek API Key（加密存储） |
| `api_base` | `https://api.deepseek.com` | API 地址 |
| `model` | `deepseek-chat` | 模型名 |
| `translation_mode` | `bilingual` | `bilingual` / `chinese_only` |
| `batch_size` | 20 | 每批翻译片段数 |
| `max_concurrency` | 5 | API 并发线程数 |
| `temperature` | 0.3 | 翻译温度 |
| `max_file_size_mb` | 500 | 上传文件大小限制（所有页面） |
| `max_concurrent_tasks` | 1 | 批量翻译并发数（仅 batch 页） |
| `ocr_enabled` | True | 启用 OCR 扫描检测 |
| `ocr_api_key` | — | OCR API Key（SiliconFlow） |
| `ocr_model` | `Qwen/Qwen3-VL-32B-Instruct` | OCR 模型 |

## Web API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传文件 |
| `/api/start/{id}` | POST | 开始翻译（支持 force 跳过重复检测） |
| `/api/start_babeldoc/{id}` | POST | BabelDOC 翻译 |
| `/api/start_ocr/{id}` | POST | OCR 翻译 |
| `/api/stop/{id}` | POST | 停止翻译 |
| `/api/progress/{id}` | GET | SSE 进度流 |
| `/api/task/{id}` | GET | 任务状态快照（JSON） |
| `/api/download/{id}` | GET | 下载 |
| `/api/languages?ui=` | GET | 语种列表 |
| `/api/config` | GET/POST | 配置读写 |
| `/api/history/check` | GET | 重复检测 |
| `/api/history/tasks` | GET/POST | 历史查询/写入 |
| `/api/history/search?q=` | GET | 文件名搜索 |
| `/api/history/dates` | GET | 日期列表 |
| `/api/history/tasks/{filename}` | DELETE | 删除历史记录 |
| `/api/book/{id}/content` | GET | EPUB 章节内容 |
| `/api/book/{id}/pdf-info` | GET | PDF 页数 |
| `/api/book/{id}/pdf-page` | GET | PDF 页面渲染 |
| `/api/book/{id}/pdf-text` | GET | PDF 文字提取 |
| `/api/book/{id}/docx-content` | GET | DOCX 内容 |
| `/settings-modal.js` | GET | 共享设置弹窗脚本 |

## 前端页面恢复机制

- 翻译中切换页面再回来 → localStorage + `/api/task/{id}` 恢复状态
- SSE 自动重连，进度条和按钮恢复
- batch 页面通过历史数据库恢复任务列表

## 关键约束

- **mimetype 必须是 ZIP 第一个文件且不压缩**（EPUB 规范）
- **不要用 `soup.prettify()`** — 直接 `str(soup)`
- **XHTML 用 XML 命名空间** — `BeautifulSoup(content, "xml")`
- `<pre>/<code>` 及 class_sch/class_skus/class_scn 不翻译
- EPUB 添加 `lang` 属性（en/zh-CN），DOCX/PDF 不加
- `**kwargs` 必须加到所有 handler 的 extract/rebuild 签名

## 依赖

```
beautifulsoup4, cryptography, easyocr, fastapi, langdetect, lxml,
numpy, opencv-python, openai, Pillow, PyMuPDF, python-docx,
python-multipart, pyyaml, tqdm, uvicorn[standard]
可选: BabelDOC (pdf_babeldoc_handler), pdf2zh (PDFMathTranslate)
