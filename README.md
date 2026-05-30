# EPUB Translator

基于 Python + DeepSeek API 的 EPUB 电子书翻译工具。将英文 EPUB 翻译为中文（或中英双语），保留原始格式、图片、链接和目录结构。

## 功能特性

- 双语模式：英文原文 + 中文翻译，保留原格式
- 仅中文模式：直接替换为中文
- 支持 HTML 表格、代码块、内联格式（粗体、斜体、链接）
- 智能跳过代码段（`<pre>`、`<code>`、CSS 类名标记的代码块）
- 翻译缓存：支持增量翻译，中断后可继续
- Web 界面：拖拽上传，实时进度，一键下载
- `lang` 属性标记：方便 CSS 区分中英文样式

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt -r server/requirements.txt
```

### 2. 配置 API Key

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入你的 API Key
```

### 3. 使用方式

**CLI 模式：**

```bash
python main.py path/to/book.epub
```

**Web 模式（推荐）：**

Windows 双击 `启动.bat`，或手动运行：

```bash
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

浏览器打开 http://localhost:8080，可在页面设置中配置 API Key。

## 项目结构

```
├── main.py                    # CLI 入口
├── config.yaml.example        # 配置模板
├── 启动.bat                   # Windows 一键启动
├── epub_translator/           # 核心库
│   ├── config.py              # 配置加载
│   ├── extractor.py           # EPUB 解压
│   ├── parser.py              # HTML/XHTML/NCX/OPF 解析
│   ├── translator.py          # DeepSeek API 翻译
│   ├── cache.py               # 翻译缓存
│   └── rebuilder.py           # EPUB 重建
├── server/                    # Web 界面
│   ├── server.py              # FastAPI 后端
│   └── index.html             # 前端页面
├── tests/                     # 测试套件
│   └── fixtures/sample.epub   # 测试 EPUB
├── CLAUDE.md                  # 项目开发文档
└── README.md                  # 本文件
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
| `skip_tags` | script, style, code, pre | 不翻译的内容 |

## CSS 中英文样式

翻译后的 HTML 为每个元素添加了 `lang` 属性：

```html
<p lang="en">English paragraph</p>
<p lang="zh-CN">中文段落</p>
```

可使用 CSS `:lang()` 伪类分别设置样式：

```css
:lang(en)    { font-family: Georgia, serif; }
:lang(zh-CN) { font-family: 'Noto Serif SC', serif; }
```

## 支持的 API

默认使用 DeepSeek API，也兼容其他 OpenAI 兼容的 API。在 `config.yaml` 中修改 `api_base` 和 `model` 即可。

## License

MIT
