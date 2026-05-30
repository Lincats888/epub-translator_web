# EPUB Translator - 基于 Python + DeepSeek 的工作计划

## 一、项目概述

构建一个 Python 工具，将英文 EPUB 电子书翻译为中文（或中英双语），保留原始格式、图片、链接和目录结构。

## 二、技术选型

| 组件 | 选型 | 原因 |
|------|------|------|
| HTML 解析 | `beautifulsoup4` + `lxml` | 稳定可靠，能保留原始格式不破坏标签结构 |
| EPUB 解压/打包 | `zipfile` (标准库) | EPUB 本质是 ZIP 文件，无需引入额外依赖 |
| 翻译 API | `openai` SDK（兼容 DeepSeek） | DeepSeek 兼容 OpenAI 接口格式 |
| 配置管理 | `PyYAML` | 可读性好，支持中文注释 |
| 增量缓存 | JSON 文件 + MD5 hash | 记录已翻译内容，避免重复翻译浪费 Token，支持断点续传 |
| 进度显示 | `tqdm` | 显示文件处理和翻译进度 |
| 测试框架 | `pytest` | Python 生态标准测试框架 |

## 三、项目目录结构

```
EpubTranslator/
├── config.yaml                  # 用户配置文件（语言模式、API Key 等）
├── main.py                      # 入口脚本
├── requirements.txt             # 依赖清单
├── task.md                      # 本计划文件
├── epub_translator/             # 核心模块包
│   ├── __init__.py
│   ├── config.py                # 配置读取与管理
│   ├── extractor.py             # EPUB 解压到 temp/
│   ├── parser.py                # HTML/XHTML 文件解析与文本提取
│   ├── translator.py            # DeepSeek API 调用与翻译逻辑
│   ├── rebuilder.py             # 翻译内容写回、EPUB 重新打包
│   └── cache.py                 # 翻译缓存管理（MD5 去重）
├── tests/                       # 测试目录
│   ├── __init__.py
│   ├── test_extractor.py        # 解压模块测试
│   ├── test_parser.py           # 解析模块测试
│   ├── test_translator.py       # 翻译模块测试
│   ├── test_rebuilder.py        # 重建模块测试
│   ├── test_cache.py            # 缓存模块测试
│   ├── test_integration.py      # 端到端集成测试
│   └── fixtures/                # 测试用 EPUB 文件
│       └── sample.epub          # 最小化的测试 EPUB
├── temp/                        # 解压后的 EPUB 内容（保留不删除）
│   └── {book_name}/
│       ├── mimetype
│       ├── META-INF/
│       │   └── container.xml
│       ├── OEBPS/ (或 OPS/)
│       │   ├── content.opf
│       │   ├── toc.ncx
│       │   ├── *.html / *.xhtml
│       │   └── images/
│       └── translation_cache.json  # 翻译缓存文件
└── output/                      # 翻译后的 EPUB 输出
    └── {book_name}_zh.epub
```

## 四、模块详细设计

### 4.1 config.py — 配置管理

- 读取 `config.yaml` 配置文件
- 配置项：
  - `api_key`: DeepSeek API Key
  - `api_base`: API 地址（默认 `https://api.deepseek.com`）
  - `model`: 模型名称（默认 `deepseek-chat`）
  - `translation_mode`: `"chinese_only"` | `"bilingual"`（中文 / 中英双语）
  - `source_language`: 源语言（默认 `English`）
  - `target_language`: 目标语言（默认 `Simplified Chinese`）
  - `batch_size`: 批量翻译的段落数（默认 5）
  - `max_retries`: API 调用失败重试次数（默认 3）

### 4.2 extractor.py — EPUB 解压

- 输入：EPUB 文件路径
- 输出：解压到 `temp/{book_name}/` 目录
- 核心逻辑：
  1. 使用 `zipfile.ZipFile` 解压 EPUB
  2. 解析 `META-INF/container.xml` 找到 OPF 文件路径
  3. 验证 EPUB 结构完整性
  4. 如果 `temp/{book_name}/` 已存在且源文件未变，跳过解压（增量支持）

### 4.3 parser.py — 内容解析

- 输入：HTML/XHTML 文件路径
- 输出：可翻译文本片段的列表，每个片段包含：
  - `file_path`: 来源文件
  - `text`: 原始文本
  - `element_id`: 元素标识（用于写回定位）
  - `context`: 上下文标签类型（`<p>`, `<h1>`, `<title>`, `<li>`, `<td>` 等）
- 核心逻辑：
  1. 使用 BeautifulSoup 解析 HTML，保持原格式
  2. 遍历所有文本节点（跳过 `<script>`, `<style>`, `<code>`, `<pre>` 中的内容）
  3. 跳过纯数字、纯标点、空字符串
  4. 对于 `<img>` 标签：保留 `alt` 属性翻译，保留 `src` 不变
  5. 对于 `<a>` 标签：保留 `href` 属性，翻译链接文本
  6. TOC 文件（`toc.ncx`）同样解析提取标题文本

### 4.4 translator.py — 翻译引擎

- 输入：文本片段列表
- 输出：翻译后的文本（与输入一一对应）
- 核心逻辑：
  1. 使用 `openai.OpenAI` 客户端连接 DeepSeek API
  2. 构建 System Prompt：
     - `chinese_only` 模式：只返回中文翻译
     - `bilingual` 模式：返回 "中文翻译\n(English: original text)" 格式
  3. 批量发送翻译请求（batch_size 可配），减少 API 调用次数
  4. 使用分隔符（如 `|||`）分隔多条文本，提示模型用同样分隔符返回
  5. 失败重试机制（指数退避）
  6. 速率控制（避免触发 API 限流）

### 4.5 rebuilder.py — 内容写回与 EPUB 重建

- 核心逻辑：
  1. 将翻译后的文本写回 BeautifulSoup 解析树中对应位置
  2. 保持原始 HTML 标签、属性、样式完全不变
  3. 对于 `toc.ncx`：写回翻译后的目录标题
  4. 更新 `content.opf` 中的元数据（如 `<dc:title>` 的翻译标题）
  5. 使用 `zipfile` 重新打包为合法 EPUB：
     - `mimetype` 必须为第一条记录且不压缩
     - 其余文件按原路径打包
  6. 输出到 `output/{book_name}_zh.epub`

### 4.6 cache.py — 翻译缓存

- 核心逻辑：
  1. 对每个待翻译文本计算 MD5 hash
  2. 缓存结构（存储为 `temp/{book_name}/translation_cache.json`）：
     ```json
     {
       "text_md5": "翻译后的文本",
       ...
     }
     ```
  3. 翻译前先查缓存，命中则跳过 API 调用
  4. 每次翻译完成后更新缓存文件
  5. 支持手动清除缓存（删除 JSON 文件即可）

## 五、配置文件示例 (config.yaml)

```yaml
# DeepSeek API 配置
api_key: "sk-xxxx"
api_base: "https://api.deepseek.com"
model: "deepseek-chat"

# 翻译模式
# "chinese_only" - 只输出中文（替换原文）
# "bilingual"    - 中英双语（保留原文 + 中文翻译）
translation_mode: "bilingual"

# 语言设置
source_language: "English"
target_language: "Simplified Chinese"

# 翻译参数
batch_size: 5          # 每次 API 调用翻译的段落数
max_retries: 3         # 失败重试次数
temperature: 0.3       # 翻译温度（低=更一致）

# 跳过的标签（不翻译其中的内容）
skip_tags:
  - script
  - style
  - code
  - pre
```

## 六、执行流程

```
用户运行: python main.py book.epub

1. 加载 config.yaml
2. 解压 EPUB → temp/{book_name}/
3. 扫描所有 HTML/XHTML + toc.ncx + content.opf
4. 对每个文件:
   a. 解析 HTML，提取可翻译文本片段
   b. 查缓存，过滤已翻译的文本
   c. 批量调用 DeepSeek API 翻译剩余文本
   d. 更新缓存
   e. 将翻译写回 HTML 标签
5. 对 toc.ncx: 翻译目录标题并写回
6. 对 content.opf: 更新元数据标题
7. 重新打包 → output/{book_name}_zh.epub
8. 输出统计: 翻译段落数、API 调用次数、Token 消耗
```

## 七、测试计划

### 7.1 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| `test_extractor.py` | 解压 EPUB 到 temp/；验证文件结构；重复解压幂等性 |
| `test_parser.py` | HTML 文本提取正确性；script/style 跳过；img/链接保留 |
| `test_translator.py` | Mock API 调用；chinese_only vs bilingual 模式；批量翻译分隔；重试逻辑 |
| `test_rebuilder.py` | 翻译文本写回；EPUB 重新打包合法性；TOC 链接有效 |
| `test_cache.py` | MD5 哈希一致性；缓存读写；命中/未命中逻辑 |

### 7.2 集成测试

- 使用最小化的测试 EPUB（`tests/fixtures/sample.epub`），包含：
  - 1 个简单的 HTML 段落
  - 1 张图片
  - 1 个内部链接
  - 简单的 TOC
- 端到端流程：解压 → 翻译 → 写回 → 打包 → 验证输出 EPUB 可被阅读器打开
- 验证 temp/ 目录在运行后保留内容
- 验证第二次运行使用缓存，不重复调用 API

### 7.3 运行测试命令

```bash
pytest tests/ -v
pytest tests/ --cov=epub_translator  # 含覆盖率
```

## 八、关键技术要点

1. **EPUB 打包顺序**：`mimetype` 必须为 ZIP 第一条记录且 `STORED`（不压缩），否则某些阅读器无法识别
2. **BeautifulSoup 格式化**：使用 `soup.prettify()` 可能改变空白字符，建议不使用格式化，直接 `str(soup)` 输出
3. **DeepSeek API 兼容性**：使用 `openai` SDK，设置 `base_url="https://api.deepseek.com"` 即可
4. **大文本分段**：对于超长段落（>2000 字符），需要智能切分（按句子边界），翻译后再拼接
5. **命名空间处理**：XHTML 文件通常有 XML 命名空间（`xmlns="http://www.w3.org/1999/xhtml"`），BeautifulSoup 需要正确配置 XML parser
6. **NCX 文件**：EPUB2 使用 `toc.ncx` 做目录，EPUB3 使用 `nav.xhtml`，需要兼容两者

## 九、实现顺序

按照依赖关系，建议实现顺序为：

1. `config.py` — 无依赖，最先完成
2. `extractor.py` — 依赖 config
3. `cache.py` — 独立模块，可并行开发
4. `parser.py` — 依赖 extractor（需要 temp 中的文件）
5. `translator.py` — 依赖 config
6. `rebuilder.py` — 依赖 parser + translator + cache
7. `main.py` — 粘合所有模块
8. 测试用例 — 与对应模块同步编写
9. `config.yaml` — 配置文件模板

## 十、待确认事项

- [x] 翻译模式默认为 `bilingual`（中英双语）
- [x] 需要断点续传（通过缓存机制实现，中断后重新运行自动跳过已翻译内容）
- [x] 需要进度条显示（使用 tqdm 库）
- [ ] 是否需要支持多个 EPUB 文件批量翻译？
