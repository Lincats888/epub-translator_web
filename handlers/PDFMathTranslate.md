# PDFMathTranslate 集成文档

> 日期：2026-06-01 | 项目：EpubTranslator

## 一、概述

集成了 [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) 作为 PDF 翻译引擎。它通过 pdfminer.six 解析 PDF 内容流（底层操作符级别），翻译后重建 PDF，排版效果显著优于我们基于 PyMuPDF bbox 的原生方案。

## 二、安装

### Windows / Linux / Mac 通用

```bash
# 方式 1: pip 直接安装
pip install pdf2zh

# 方式 2: uv 安装 (推荐，自动管理 Python 3.12 环境)
pip install uv
uv tool install --python 3.12 pdf2zh
```

> **注意**: pdf2zh 需要 Python 3.10-3.12。如果当前 Python 是 3.13+，必须用 uv 方式安装。

### 启动脚本自动部署

- Windows: `启动.bat` 步骤 [2/4] 自动检测并安装
- Linux/Mac: `启动.sh` 步骤 [2/4] 自动检测并安装

## 三、API Key 配置

PDFMathTranslate 使用独立的配置文件（不读我们的 `config.yaml`）。

**配置文件路径**:
- Windows: `C:\Users\<用户名>\.config\PDFMathTranslate\config.json`
- Linux/Mac: `~/.config/PDFMathTranslate/config.json`

**配置内容**:
```json
{
    "PDF2ZH_LANG_FROM": "English",
    "PDF2ZH_LANG_TO": "Simplified Chinese",
    "translators": [
        {
            "name": "deepseek",
            "envs": {
                "DEEPSEEK_API_KEY": "sk-你的API密钥",
                "DEEPSEEK_MODEL": "deepseek-chat"
            }
        }
    ]
}
```

> **注意**: 这是一个 JSON **对象**，不是数组。写成 `[{...}]` 会导致 `TypeError: list indices must be integers or slices, not str`。

## 四、代码集成

### 4.1 核心入口

`handlers/pdf_handler.py`:

```python
# 自动查找 pdf2zh 二进制（跨平台）
PdfHandler._find_pdf2zh()       # → 返回路径或 None
PdfHandler.is_pdf2zh_available()  # → True/False

# 直接调用翻译
output = PdfHandler.rebuild_via_pdf2zh(
    "book.pdf",
    output_dir="output",
    service="deepseek"
)
# → 返回 output/book_dual.pdf
```

### 4.2 服务端集成

`server/server.py` - `_run_translate_generic()`:

```python
def _run_translate_generic(..., pdf_method="pdf2zh"):
    if pdf_method == "pdf2zh":
        # 快速路径: 跳过我们的提取+翻译+重建流程
        # 直接委托给 PDFMathTranslate
        output_path = PdfHandler.rebuild_via_pdf2zh(file_path, output_dir=...)
        # pdf2zh 内部处理一切: 提取、翻译、排版、输出
        return
    # ... 否则走原生流程
```

`/api/start` 端点接收 `pdf_method` 参数:
- `"pdf2zh"` — PDFMathTranslate 引擎（默认）
- `"native"` — 原生 PyMuPDF 引擎

### 4.3 前端选择器

`server/index.html` — PDF 上传后显示引擎下拉框:

```html
<select id="pdf-method" style="display:none">
    <option value="pdf2zh">引擎: PDFMathTranslate</option>
    <option value="native">引擎: 原生</option>
</select>
```

- 仅当上传文件后缀为 `.pdf` 时显示
- 默认选中 `pdf2zh`
- 非 PDF 文件不显示此下拉框

## 五、两种引擎对比

| | PDFMathTranslate | 原生 (Native) |
|---|---|---|
| 原理 | pdfminer 解析内容流 → 重建 PDF | PyMuPDF redact + fill_textbox |
| 排版 | 内容流级重建，自由换行 | 受限于原文 bbox，缩字适配 |
| 字号 | 保持可读 | 可能缩小到 6-8pt |
| 公式保护 | ✅ DocLayout-YOLO AI 检测 | ❌ |
| 速度 | 2-3 秒/页 | 更快（轻量） |
| 依赖 | Python 3.10-3.12, uv, ONNX | 仅 PyMuPDF |
| 适用 | 需要好排版的 PDF | 简单 PDF 或 python 3.13+ |

## 六、PDFMathTranslate 源码架构

（供深度定制参考）

- **入口**: `pdf2zh.high_level.translate()` → `translate_stream()` → `translate_patch()`
- **PDF 解析**: pdfminer.six → `PDFPageInterpreterEx` 获取底层内容流操作符
- **翻译**: `TranslateConverter` 三阶段管道（解析→翻译→重建）
- **布局检测**: DocLayout-YOLO ONNX 模型识别公式/图表区域
- **缓存**: SQLite (Peewee ORM) 持久化翻译结果
- **字体**: SourceHanSerifCN + fontTools 子集化

> **关键纠正**: 网上中文博客说 PDFMathTranslate 用 ReportLab — **这是错的**。它用 pdfminer.six 解析 + 自定义 PDF 操作符生成，不依赖 ReportLab。

## 七、故障排查

| 问题 | 解决方法 |
|------|---------|
| `pdf2zh: command not found` | 用 `uv tool install --python 3.12 pdf2zh` 安装 |
| `TypeError: list indices must be integers` | config.json 是数组 `[{...}]`，应改为对象 `{...}` |
| Python 3.13 无法 import pdf2zh | pdf2zh pip 版(1.7.x)支持有限，用 uv 装 1.9.x |
| `Text must start in rectangle` | write_rect 太小，原生引擎中有 min(20,20) 保底 |
| 翻译后界面卡住无法下载 | 文件在 `temp/pdf2zh_output/` 或输入文件同目录下的 `*-dual.pdf` |
| Windows 缺少 VCRUNTIME140.dll | 安装 [VC Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe) |

## 八、相关文件清单

```
handlers/pdf_handler.py        # PdfHandler.rebuild_via_pdf2zh(), _find_pdf2zh()
handlers/PDFMathTranslate.md   # 本文档
server/server.py               # _run_translate_generic() pdf2zh 快速路径
server/index.html              # pdf-method 下拉框
启动.bat                       # Windows 启动 + 自动安装
启动.sh                        # Linux/Mac 启动 + 自动安装
config.yaml                    # 我们的配置（不影响 pdf2zh）
~/.config/PDFMathTranslate/config.json  # pdf2zh 独立配置
```
