# BabelDOC 集成手册 —— 回家操作版

> 适用于家中已集成 PDFMathTranslate 的项目。
> 不需要覆盖任何现有文件，逐步手动修改。

---

## 📦 要拷贝的文件（放 U 盘）

| # | 文件 | 放哪里 | 说明 |
|---|------|--------|------|
| 1 | `handlers/pdf_babeldoc_handler.py` | `handlers/` | **新文件**，直接复制 |
| 2 | `BabelDOC_集成指南.md` | 项目根目录 | 本手册 |

> 不修改 `handlers/__init__.py`、`handlers/pdf_handler.py` 等文件。

---

## 在家里的操作

### 第 1 步：安装 BabelDOC

```bash
# 国内用阿里云镜像
pip install BabelDOC -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com

# 验证
python -c "import babeldoc; print(babeldoc.__version__)"
# 应输出 0.6.2
```

### 第 2 步：下载模型和字体

```bash
python -c "from babeldoc.assets.assets import warmup; warmup()"
```
等待完成（约 5-10 分钟，下载 ~330MB 数据）。

缓存位置：`C:\Users\<你的用户名>\.cache\babeldoc\`
- `models/` — ONNX 布局模型
- `fonts/` — 34 个 TTF 字体 + font_metadata.json
- `cmap/` — 146 个 CMap 文件
- `tiktoken/` — tokenizer 缓存

### 第 3 步：复制新文件

把 U 盘里的 `handlers/pdf_babeldoc_handler.py` 复制到家里项目的 `handlers/` 目录下。

### 第 4 步：修改 server/server.py

打开家里的 `server/server.py`，做 **2 处改动**：

#### 4.1 在 `_run_translate_generic` 函数后面（在它最后一个 `except` 块之后），加上以下函数：

```python
def _run_translate_babeldoc(task_id: str, file_path: str, target_lang: str = "zh-CN",
                             bilingual: bool = True):
    """Background PDF translation using BabelDOC engine (one-shot IL pipeline)."""
    try:
        _update(task_id, status="loading", step="Loading configuration...")
        config = Config(CONFIG_PATH)
        config.load()
        if not config.api_key or config.api_key in ("sk-xxxx", "sk-your-api-key-here"):
            _update(task_id, status="error", error="API key not configured. Click settings.")
            return

        _update(task_id, step="Initializing BabelDOC...")

        from handlers.pdf_babeldoc_handler import PdfBabeldocHandler
        handler = PdfBabeldocHandler()

        # Build base_url with /v1 suffix (Config stores base without /v1)
        base_url = config.api_base.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"

        def progress_callback(stage, pct, msg):
            # Map BabelDOC stages to the SSE fields the frontend expects
            if stage in ("init", "loading"):
                _update(task_id, status="loading", step=msg, file_name="Preparing...")
            elif stage in ("translate", "translating"):
                _update(task_id, status="translating", step=msg,
                        file_name=os.path.basename(file_path),
                        file_progress=pct, file_total=100,
                        total_files=1, current_file=1)
            elif stage == "done":
                _update(task_id, status="done", message=msg)
            elif stage == "error":
                _update(task_id, status="error", error=msg)

        output = handler.translate_full(
            file_path=file_path,
            target_lang=target_lang,
            source_lang="en",
            api_key=config.api_key,
            base_url=base_url,
            model=config.model or "deepseek-chat",
            bilingual=bilingual,
            output_dir=OUTPUT_DIR,
            progress_callback=progress_callback,
        )

        _update(task_id, status="done", step="Complete!",
                output=output, translated=0, cached=0)

    except Exception as e:
        _update(task_id, status="error", error=str(e))
```

#### 4.2 在 `@app.post("/api/start/{task_id}")` 路由后面（在 `start_translation` 函数结束之后、`@app.post("/api/stop/{task_id}")` 之前），加上以下路由：

```python
@app.post("/api/start_babeldoc/{task_id}")
async def start_babeldoc_translation(task_id: str, body: dict = None):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("status") == "translating":
        raise HTTPException(400, "Already translating")
    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(404, "File not found")

    target_lang = "zh-CN"
    bilingual = True
    if body:
        target_lang = body.get("target_lang", target_lang)
        bilingual = body.get("bilingual", True)

    _update(task_id, status="queued", step="Starting BabelDOC...", stopped=False,
            target_lang=target_lang, bilingual=bilingual)

    _executor.submit(_run_translate_babeldoc, task_id, file_path,
                     target_lang, bilingual)

    return {"ok": True}
```

### 第 5 步：修改 server/index.html

打开家里的 `server/index.html`，做 **3 处改动**：

#### 5.1 添加引擎选择下拉框

找到翻译模式选择框 `<select id="trans-mode">`，在它**前面**插入：

```html
<select id="trans-engine" style="padding:6px 12px; border:1px solid var(--border); border-radius:8px; font-family:var(--sans); font-size:13px; background:var(--bg); color:var(--text); outline:none; cursor:pointer;">
  <option value="native">原生引擎</option>
  <option value="babeldoc">BabelDOC</option>
</select>
```

最终效果：
```html
<select id="target-lang">...</select>
<select id="trans-engine">          ← 新增
  <option value="native">原生引擎</option>
  <option value="babeldoc">BabelDOC</option>
</select>
<select id="trans-mode">
  <option value="bilingual">双语</option>
  <option value="target_only">目标语言</option>
</select>
```

#### 5.2 修改 toggleTranslation() 函数的路由选择

在 JS 中找到 `toggleTranslation()` 函数，找到以下代码：
```javascript
fetch(`/api/start/${taskId}`,{
```

**改成：**
```javascript
const engine = document.getElementById('trans-engine').value;
const apiRoute = (engine === 'babeldoc') ? `/api/start_babeldoc/${taskId}` : `/api/start/${taskId}`;
fetch(apiRoute,{
```

#### 5.3 引擎选择仅 PDF 文件显示

在 `showBook()` 函数中，`showStartButton();` 这一行**前面**，插入：

```javascript
// Show engine selector only for PDF
const engineSel = document.getElementById('trans-engine');
const fileType = (data.filename || '').toLowerCase();
if (fileType.endsWith('.pdf')) {
  engineSel.style.display = '';
} else {
  engineSel.style.display = 'none';
}
```

### 第 6 步：启动测试

```bash
python -m uvicorn server.server:app --host 127.0.0.1 --port 8080
```

浏览器打开 `http://localhost:8080`，上传 PDF 文件：
- 选择 **BabelDOC** 引擎 → 开始翻译 → 观察进度条
- 选择 **原生引擎** → 走原来的 PDFMathTranslate

---

## 🧪 命令行测试（可选）

如果想先不启动 Web 页面，直接在命令行测试：

```bash
python -c "
from handlers.pdf_babeldoc_handler import PdfBabeldocHandler
from epub_translator.config import Config

cfg = Config('config.yaml')
cfg.load()

base_url = cfg.api_base.rstrip('/')
if not base_url.endswith('/v1'):
    base_url += '/v1'

def cb(stage, pct, msg):
    print(f'[{stage}] {pct}% - {msg}')

handler = PdfBabeldocHandler()
output = handler.translate_full(
    file_path='要翻译的PDF文件.pdf',   # ← 改成你的 PDF 路径
    target_lang='zh-CN',
    source_lang='en',
    api_key=cfg.api_key,
    base_url=base_url,
    model=cfg.model or 'deepseek-chat',
    bilingual=True,
    output_dir='./output',
    progress_callback=cb,
)
print('输出:', output)
"
```

---

## 🔙 回退方法

```bash
pip uninstall BabelDOC -y              # 卸载
del handlers\pdf_babeldoc_handler.py   # 删除 Handler
```

然后从 `server.py` 删除 `_run_translate_babeldoc` 函数和 `/api/start_babeldoc/` 路由。
从 `index.html` 删除 `trans-engine` 下拉框和相关 JS。

---

## ⚠️ 注意事项

| 项目 | 说明 |
|------|------|
| API Base URL | Config 中是 `https://api.deepseek.com`，BabelDOC 需要 `https://api.deepseek.com/v1`。Handler 内部自动拼接 `/v1` |
| extract() 不可用 | BabelDOC 是一站式流程，不支持分段 extract/rebuild |
| 首次运行慢 | 需要加载 ONNX 模型 + 版面分析，后续翻译较快 |
| 缓存位置 | `C:\Users\<用户名>\.cache\babeldoc\`，约 330 MB |
| 许可证 | AGPL-3.0（与 PyMuPDF 一致） |
| 进度条 | 基于时间估算轮询（版面分析→AI翻译→排版渲染），非真实进度 |
| PDFMathTranslate | **不受影响**。BabelDOC 是独立路由 `/api/start_babeldoc/`，不冲突 |
