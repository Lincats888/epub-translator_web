# PDF DOM 研究提纲

> 目标：理解 PDF 文字渲染的底层机制，为自研 PDF 翻译引擎打基础

---

## 一、PDF 内容流基础

### 1.1 什么是内容流
PDF 页面内容是一串**后缀表达式操作符**：

```
BT                          % Begin Text — 进入文字模式
  /F1 12 Tf                 % 选 F1 字体，12pt
  100 700 Td                % 移动到 (100, 700)
  0.5 Ts                    % 字间距 0.5pt
  (Hello) Tj                % 画 "Hello"
  (World) '                 % 换行 + 画 "World"
ET                          % End Text — 退出文字模式
```

**不是标记语言**——没有 `<p>`, `<div>`, 只有画布上的指令。

### 1.2 关键参考资料
- [PDF 1.7 Reference, Chapter 9: Text](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.7old.pdf)
- [pdfminer.six 源码](https://github.com/pdfminer/pdfminer.six)
- [PyMuPDF (fitz) 源码](https://github.com/pymupdf/PyMuPDF)
- [PDFMathTranslate 源码](https://github.com/Byaidu/PDFMathTranslate)

---

## 二、核心文字操作符

### 2.1 字体选择 — `Tf`

```
/FontName fontSize Tf
```

- `FontName`：字体资源名（如 `/F1`, `/C2_0`）
- `fontSize`：字号（pt）
- 字体资源在页面 `/Resources` 字典中定义

### 2.2 文字定位 — `Td`, `TD`, `Tm`, `T*`

| 操作符 | 语法 | 说明 |
|--------|------|------|
| `Td` | `tx ty Td` | 平移到 (tx, ty)，相对于当前文字行 |
| `TD` | `tx ty TD` | 同 Td，但将 ty 设为新的行间距 |
| `Tm` | `a b c d e f Tm` | **设置文字变换矩阵**（2D 仿射变换） |
| `T*` | `T*` | 换行（移动到下一行起始） |

**Tm 矩阵** 是最重要的——它控制文字的：
- 位置 (`e`, `f` = x, y 坐标)
- 缩放 (`a`, `d` = 水平和垂直缩放)
- 倾斜/旋转 (`b`, `c`)

```
a b 0
c d 0
e f 1
```

正常水平文字：`1 0 0 1 x y Tm`（a=1, b=0, c=0, d=1）

### 2.3 文字绘制 — `Tj`, `TJ`, `'`, `"`

| 操作符 | 语法 | 说明 |
|--------|------|------|
| `Tj` | `(string) Tj` | 画一段文字 |
| `TJ` | `[(Hello) -5 (World)] TJ` | 画多段文字，数字是**字间距调整**（千分之一 em） |
| `'` | `(string) '` | 换行 + 画文字 |
| `"` | `aw ac (string) "` | 设字间距 + 字符间距 + 换行 + 画文字 |

**关键**：`TJ` 里的数字是 **glyph positioning adjustments**——负数缩小间距，正数扩大间距。

---

## 三、字体与编码

### 3.1 PDF 字体的三种类型

| 类型 | 编码方式 | 中文支持 |
|------|---------|---------|
| **Type 1 (PostScript 简单字体)** | 单字节，最多 256 个字形 | ❌ 不支持 |
| **TrueType / OpenType** | CMap 或 CID 编码 | ✅ |
| **CIDFont (Type 0 复合字体)** | CID → GID → 字形 | ✅ 最常见于中文 PDF |

### 3.2 CMap 和 ToUnicode

- **CMap** (`/ToUnicode`)：字形索引 → Unicode 字符的映射表
- 没有 CMap 的 PDF，文字提取只能靠 OCR 或字形名猜测
- 翻译后写回时，需要**重新生成 ToUnicode CMap** 或使用 CID 字体

### 3.3 CID 字体写回问题

这是最棘手的部分：
1. 原文使用 CID 字体（如 `/C2_0`），每个字符用 CID 值索引
2. 中文翻译的字符可能不在原文字体的 CID 范围
3. 解决方案：
   - **嵌入新字体**（PDFMathTranslate 的做法：嵌入 SourceHanSerifCN）
   - 生成**字体子集**（只包含翻译用到的字符）
   - 更新页面 `/Resources` 字典

---

## 四、pdfminer.six 解析流程

### 4.1 核心类

```
PDFParser → PDFDocument → PDFPage → PDFInterpreter → PDFConverter
```

- `PDFParser`：读取 PDF 二进制，解析交叉引用表（xref）
- `PDFInterpreter`：遍历页面内容流，执行每个操作符
- `PDFConverter`：接收操作符事件，构建高级对象（LTPage, LTTextBox...）

### 4.2 关键事件回调

```python
class PDFConverter(PDFContentParser):
    def render_string(self, textstate, text, ncs, graphicstate):
        # 在 (x, y) 位置画一段文字
        ...
    
    def render_rectangle(self, graphicstate, x, y, w, h):
        # 画矩形（背景色块等）
        ...
```

### 4.3 PDFMathTranslate 的使用方式

```
1. PDFResourceManager     → 管理字体/资源缓存
2. TranslateConverter     → 继承 PDFConverter，在 render_string 中拦截文字
3. PDFPageInterpreterEx   → 继承 PDFPageInterpreter，逐操作符处理
4. interpreter.process_page(page)  → 触发整页解析
```

---

## 五、文字写回方案

### 5.1 方案 A：修改 TJ/Tj 操作符（PDFMathTranslate 做法）

```
原始: [(Hello) -5 (World)] TJ
翻译后: [(你好) -2 (世界)] TJ
```

- 优点：保留原始排版结构
- 难点：中文需要的 glyph positioning 不同，Tm 可能需要调整

### 5.2 方案 B：生成新的内容流

```
1. 收集所有文字的位置和样式
2. 翻译
3. 用 ReportLab / 原生 PDF 写回生成新的内容流
```

- 优点：完全控制排版
- 难点：需要重建字体、图片、矢量图形等非文字元素

### 5.3 方案 C：混合（我们当前的做法 + 增强）

```
PyMuPDF 提取 block → 翻译 → 用 redact + 新字体写回
```

- 优点：简单，不碰内容流
- 缺点：受限于原文 bbox

---

## 六、待攻克的关键问题

| 问题 | 难度 | 现状 |
|------|------|------|
| CID 字体替换 | ⭐⭐⭐⭐⭐ | pdfminer 可提取 → 需自研写回 |
| TJ 数组重建 | ⭐⭐⭐⭐ | pdf2zh 已实现，可参考 |
| Tm 矩阵调整（缩放） | ⭐⭐⭐⭐ | 需要在写回时按比例缩放 |
| 跨行文字分段 | ⭐⭐⭐ | pdfminer 提供 LTTextBox 分组 |
| 图形/图片保留 | ⭐⭐⭐⭐⭐ | 需遍历所有非文字操作符 |
| 矢量图形中的文字 | ⭐⭐⭐⭐⭐ | 如箭头标签、流程图 |

---

## 七、建议研究路径

**阶段 1**（1-2天）：读懂 PDF 内容流
- 用 `mutool show -p stream page.pdf` 查看原始内容流
- 用 pdfminer 的 `pdf2txt.py -t xml` 查看解析结果
- 手写一个简单的 PDF 页面（只用操作符画文字）

**阶段 2**（3-5天）：理解 pdfminer.six 架构
- 读 `PDFPageInterpreter.process_page()` 源码
- 读 `PDFConverter.render_string()` 源码
- 写一个最小 `PDFConverter` 子类，拦截所有文字

**阶段 3**（5-10天）：实现文字写回
- 在 `render_string` 中替换文字
- 处理 TJ 数组的字间距调整
- 嵌入 CJK 字体并更新 Resources

**阶段 4**（持续）：打磨
- 处理旋转文字（Tm 含旋转分量）
- 处理多栏布局
- 处理表格和列表

---

## 八、环境准备

```bash
# 工具
pip install pdfminer.six pymupdf fonttools

# 查看 PDF 原始内容流
mutool show -p stream page.pdf    # mupdf-tools
# 或
python -c "
import fitz
doc = fitz.open('page.pdf')
print(doc[0].get_text('rawdict'))
"

# 用 pdfminer 导出 XML 结构
pdf2txt.py -t xml -o output.xml page.pdf
```
