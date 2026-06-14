# Markdown 导出方案

## 需求

翻译完成后（EPUB / DOCX / PDF），额外输出目标语言 Markdown 文件到 `output/markdown/<书名>/`。即使翻译选择了双语模式，Markdown 也只包含目标语言。

## 可行性分析

| 格式 | 引擎 | 翻译后数据 | Markdown 质量 |
|------|------|-----------|:---:|
| EPUB | — | `extract_dir` 中已翻译 HTML，结构完整（h1~h6, p, table, li） | ⭐⭐⭐ |
| DOCX | — | `fragments` + `translations`，`meta.style` 含标题级别，`meta.table/row/col` 含表格坐标 | ⭐⭐⭐ |
| PDF | Native PyMuPDF | `fragments` + `translations`，`meta` 含页面/bbox 位置信息 | ⭐⭐ |
| PDF | BabelDOC | 只有输出 PDF 路径 | ⭐ 事后 PyMuPDF 提取 |
| PDF | pdf2zh | 只有输出 PDF 路径 | ⭐ 事后 PyMuPDF 提取 |

## 架构

新增独立模块 `handlers/markdown_exporter.py`，最小化对现有文件的修改。

```
MarkdownExporter
├── export_epub(extract_dir, output_dir, book_name)
│     遍历 extract_dir 中翻译后的 HTML，每章一个 .md
│     双语模式下只提取 [lang="zh-CN"] 元素
│     表格 cell 提取 <br/> 后的译文
│
├── export_docx(fragments, translations, output_dir, book_name)
│     用 meta.style（Heading 1/2/3）映射 #/##/###
│     用 meta.table/row/col 重建 Markdown 表格
│     单文件输出（段落顺序即是文档顺序）
│
├── export_pdf_native(fragments, translations, output_dir, book_name)
│     按 meta.page 分组 + 按 meta.bbox.y 排序
│     页面间加 --- 分隔
│     单文件输出
│
└── export_pdf_post_hoc(output_pdf_path, output_dir, book_name)
       fitz.open() → page.get_text() 逐页提取
       页面间加 --- 分隔
       覆盖 BabelDOC / pdf2zh 引擎
```

依赖：仅用已有的 `BeautifulSoup` + `fitz`，不引入新库。

## 修改点

### 1. 新增文件

`handlers/markdown_exporter.py` — 完整模块

### 2. server/server.py（2 处，各约 5 行）

- `_run_translate()` 末尾（EPUB 翻译完成处）：
  ```python
  if task_export_markdown:
      MarkdownExporter.export_epub(extract_dir, OUTPUT_DIR, book_name)
  ```

- `_run_translate_generic()` 末尾（PDF/DOCX 翻译完成处）：
  ```python
  if task_export_markdown:
      if is_docx:
          MarkdownExporter.export_docx(fragments, all_translations, OUTPUT_DIR, book_name)
      elif is_pdf_native:
          MarkdownExporter.export_pdf_native(fragments, all_translations, OUTPUT_DIR, book_name)
      else:  # BabelDOC / pdf2zh
          MarkdownExporter.export_pdf_post_hoc(output_path, OUTPUT_DIR, book_name)
  ```

### 3. server/translate.html（1 处）

在翻译选项区加复选框：
```html
<input type="checkbox" id="exportMarkdown"> 导出 Markdown
```
提交时带 `export_markdown: true/false`

### 4. API 参数

`POST /api/start/{task_id}` 的 body 新增可选字段 `export_markdown: bool`

## 输出目录结构

```
output/
  markdown/
    <书名>/
      chapter_01.md
      chapter_02.md
      ...
```

## 不做的

- 批次翻译页不加此选项
- 不引入新的 Python 依赖
- 不修改 BaseHandler 接口
