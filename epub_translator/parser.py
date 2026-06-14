import re
import os
from dataclasses import dataclass, field
from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass
class TextFragment:
    """A translatable text fragment with its location for later write-back.

    element type:
      - chinese_only mode: NavigableString (replaced in-place)
      - bilingual mode (normal): Tag (cloned and inserted after original)
      - bilingual mode (table cell): <td>/<th> Tag (translation written directly in cell)
      - code mode: <pre>/<code> Tag (translate only comments, keep code as-is)
    """

    text: str
    element: NavigableString | Tag
    is_table: bool = False
    table_cells: list = field(default_factory=list)
    is_code: bool = False


@dataclass
class ParsedFile:
    """Represents a parsed file with its soup tree and translatable fragments."""

    file_path: str
    soup: BeautifulSoup
    fragments: list[TextFragment] = field(default_factory=list)
    original_content: str = ""
    bilingual: bool = False
    is_xml: bool = False

    def write_back(self, translations: list[str]) -> str:
        """Write translated texts back and return the modified content."""
        if self.bilingual:
            for fragment, translation in zip(self.fragments, translations):
                if fragment.is_code:
                    elem = fragment.element
                    elem[TRANSLATION_MARKER] = "1"
                    elem["lang"] = "en"
                    cloned = self.soup.new_tag(elem.name)
                    if hasattr(elem, "attrs"):
                        for k, v in elem.attrs.items():
                            cloned[k] = v
                    if hasattr(elem, "prefix") and elem.prefix:
                        cloned.prefix = elem.prefix
                    cloned[TRANSLATION_MARKER] = "1"
                    cloned["lang"] = "zh-CN"
                    cloned.string = translation
                    elem.insert_after(cloned)
                elif _within_tags(fragment.element, {"table"}):
                    # Table cells or content inside tables: write translation
                    # directly (no clone) to keep table structure intact.
                    # Use <br/> for visual line break between original and Chinese.
                    elem = fragment.element
                    elem[TRANSLATION_MARKER] = "1"
                    elem.append(self.soup.new_tag("br"))
                    _append_html(elem, translation, self.soup)
                elif _within_tags(fragment.element, {"navLabel"}):
                    # NCX TOC entries (<text> inside <navLabel>):
                    # append translation as text — cloning would create
                    # duplicate <text> elements which breaks the NCX schema.
                    elem = fragment.element
                    elem[TRANSLATION_MARKER] = "1"
                    original = elem.get_text(strip=True)
                    if elem.string is not None:
                        elem.string = original + "\n" + translation
                    else:
                        elem.append("\n" + translation)
                elif isinstance(fragment.element, NavigableString):
                    # Direct text in a mixed-content block element:
                    # wrap translation in <span> and insert after original.
                    ns = fragment.element
                    span = self.soup.new_tag("span")
                    span["lang"] = "zh-CN"
                    span[TRANSLATION_MARKER] = "1"
                    span.string = translation
                    ns.insert_after(span)
                elif not _is_leaf_block(fragment.element):
                    # Non-leaf block (e.g., <div> with text + <table>,
                    # or <li> with sub-list): create a CLEAN clone with just
                    # the translation — do NOT clone nested blocks.
                    elem = fragment.element
                    wrapper = self.soup.new_tag(elem.name)
                    if hasattr(elem, "attrs"):
                        for k, v in elem.attrs.items():
                            if k != "id":  # avoid duplicate id
                                wrapper[k] = v
                    if hasattr(elem, "prefix") and elem.prefix:
                        wrapper.prefix = elem.prefix
                    elem[TRANSLATION_MARKER] = "1"
                    elem["lang"] = "en"
                    wrapper[TRANSLATION_MARKER] = "1"
                    wrapper["lang"] = "zh-CN"
                    # Preserve <a href> from original if the translator
                    # didn't keep the link in its output.
                    _write_translation(wrapper, translation, elem, self.soup)
                    elem.insert_after(wrapper)
                else:
                    elem = fragment.element
                    cloned = self.soup.new_tag(elem.name)
                    if hasattr(elem, "attrs"):
                        for k, v in elem.attrs.items():
                            cloned[k] = v
                    if hasattr(elem, "prefix") and elem.prefix:
                        cloned.prefix = elem.prefix
                    elem[TRANSLATION_MARKER] = "1"
                    elem["lang"] = "en"
                    cloned[TRANSLATION_MARKER] = "1"
                    cloned["lang"] = "zh-CN"
                    _write_translation(cloned, translation, elem, self.soup)
                    elem.insert_after(cloned)
        else:
            # Non-bilingual (chinese_only): replace block element's inner content
            # with the translated HTML (inline tags preserved).
            for fragment, translation in zip(self.fragments, translations):
                if fragment.is_code:
                    fragment.element.clear()
                    fragment.element.string = translation
                    fragment.element["lang"] = "zh-CN"
                elif isinstance(fragment.element, Tag):
                    elem = fragment.element
                    if not _is_leaf_block(elem):
                        # Non-leaf block (e.g., <div> with text + <table>):
                        # replace only NavigableString children, keep
                        # nested blocks like <table> intact.
                        first_ns = None
                        for child in list(elem.children):
                            if isinstance(child, NavigableString):
                                t = str(child).strip()
                                if t:
                                    if first_ns is None:
                                        first_ns = child
                                    else:
                                        child.extract()
                                else:
                                    child.extract()
                        if first_ns is not None:
                            first_ns.replace_with(translation)
                        elem[TRANSLATION_MARKER] = "1"
                    else:
                        elem.clear()
                        _append_html(elem, translation, self.soup)
                        elem[TRANSLATION_MARKER] = "1"
                else:
                    # NavigableString (NCX/OPF files in non-bilingual mode)
                    fragment.element.replace_with(translation)
        return str(self.soup)

    def save(self, translations: list[str]):
        """Write translated content back to the original file."""
        content = self.write_back(translations)
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write(content)


# Attribute added to cloned elements so the parser skips them on re-runs
TRANSLATION_MARKER = "data-epub-translator"

# Block-level tags used for bilingual mode extraction
BLOCK_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "ul",
        "dt",
        "dd",
        "td",
        "th",
        "figcaption",
        "blockquote",
        "div",
        "section",
        "article",
        "aside",
    }
)

# Tags that contain code — comments inside are translated, code stays as-is
CODE_TAGS = frozenset({"pre", "code"})


def _should_skip_tag(tag: Tag, skip_tags: list[str]) -> bool:
    """Check if this tag or any ancestor should be skipped."""
    current = tag
    while current is not None:
        if hasattr(current, "name") and current.name in skip_tags:
            return True
        current = current.parent if hasattr(current, "parent") else None
    return False


def _within_tags(tag: Tag, tag_names: set[str]) -> bool:
    """Check if this tag or any ancestor has one of the given tag names."""
    current = tag
    while current is not None:
        if hasattr(current, "name") and current.name in tag_names:
            return True
        current = current.parent if hasattr(current, "parent") else None
    return False


def _has_ancestor_with_marker(tag: Tag) -> bool:
    """Check if any ancestor has the TRANSLATION_MARKER attribute."""
    current = tag.parent if hasattr(tag, "parent") else None
    while current is not None:
        if hasattr(current, "get") and current.get(TRANSLATION_MARKER):
            return True
        current = current.parent if hasattr(current, "parent") else None
    return False


def _contains_code(tag: Tag) -> bool:
    """Check if this element is predominantly a code block (not just inline code).

    If >50% of text is inside code/pre tags, treat as code block and skip.
    Paragraphs with occasional inline <code> (like file paths) are NOT skipped.
    """
    if hasattr(tag, "name") and tag.name in ("code", "pre"):
        return True
    # Count total text vs text inside code tags
    total_len = len(tag.get_text())
    if total_len == 0:
        return False
    code_text_len = 0
    for child in tag.find_all(["code", "pre"]):
        code_text_len += len(child.get_text())
    # If >50% of the block is code content, treat as code block
    if code_text_len > total_len * 0.5:
        return True
    # Also check CSS-based code classes
    _CODE_CLASSES = ("class_sch", "class_skus", "class_scn")

    def _has_code_class(elem):
        cls = elem.get("class", [])
        if isinstance(cls, str):
            return any(c in cls for c in _CODE_CLASSES)
        return any(c in cls for c in _CODE_CLASSES)

    if _has_code_class(tag):
        return True
    for child in tag.find_all(True):
        if _has_code_class(child):
            return True
    return False


def _append_html(target: Tag, html: str, soup: BeautifulSoup):
    """Append HTML content to a tag, preserving inline elements like <a>, <strong>, <em>.

    If html is plain text (no tags), appends it directly without creating extra elements.
    If html contains inline tags, parses and appends them preserving the structure.
    """
    # Check if html contains any HTML tags
    if "<" not in html:
        # Plain text — append directly, no parsing
        target.append(html)
        return
    try:
        # Use "html.parser" to avoid lxml auto-wrapping in <p>/<html>/<body>
        parsed = BeautifulSoup(html, "html.parser")
        for child in list(parsed.contents):
            target.append(child)
    except Exception:
        target.append(html)


def _is_translatable(text: str) -> bool:
    """Check if text is worth translating (contains actual language content)."""
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) < 2:
        return False
    # Must contain at least one letter character (any script: Latin, CJK, etc.)
    if not any(c.isalpha() for c in stripped):
        return False
    return True


def _contains_comment(text: str) -> bool:
    """Check if text (from a code block) contains programming comments."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("--"):
            return True
    if re.search(r"/\*[\s\S]*?\*/", text):
        return True
    return False


def _write_translation(target: Tag, translation: str, source: Tag, soup: BeautifulSoup):
    """Append translation to target, preserving <a href> from source if needed.

    If the translation already contains an <a> tag (the AI kept the link),
    parse it as-is.  Otherwise, wrap the translation in a new <a> that
    copies the href from the source element's first <a> tag.
    """
    if "<a " in translation.lower():
        _append_html(target, translation, soup)
        return
    a_tag = source.find("a")
    if a_tag and a_tag.get("href"):
        new_a = soup.new_tag("a")
        for k, v in a_tag.attrs.items():
            new_a[k] = v
        new_a.string = translation
        target.append(new_a)
    else:
        _append_html(target, translation, soup)


def _is_leaf_block(elem: Tag) -> bool:
    """True if this block element doesn't contain other block-level elements.

    NOTE: Block tags nested inside <table> are ignored — table cells are
    extracted separately, so <p> inside <td> should not block the parent div.
    """
    for child in elem.find_all():
        if child is elem:
            continue
        if not hasattr(child, "name"):
            continue
        if child.name in BLOCK_TAGS:
            # Don't let table-internal <p>/<th>/<td> block parent extraction
            if _within_tags(child, {"table"}):
                continue
            return False
    return True


def parse_html_file(
    file_path: str, skip_tags: list[str], bilingual: bool = False
) -> ParsedFile:
    """Parse an HTML/XHTML file and extract translatable text fragments."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    is_xhtml = file_path.lower().endswith(".xhtml") or file_path.lower().endswith(".xml")
    use_xml = is_xhtml or "xmlns" in content[:500]
    soup = BeautifulSoup(content, "xml" if use_xml else "lxml")

    result = ParsedFile(
        file_path=file_path, soup=soup, original_content=content, bilingual=bilingual,
        is_xml=use_xml,
    )

    # Process code blocks: only translate if they contain comments
    for code_tag in soup.find_all(CODE_TAGS):
        if code_tag.get(TRANSLATION_MARKER):
            continue
        parent = code_tag.parent
        if parent and hasattr(parent, "name") and parent.name in CODE_TAGS:
            continue
        # Skip if inside script or style
        if _within_tags(code_tag, {"script", "style"}):
            continue
        # Skip if code/pre is in skip_tags (code should not be translated)
        if _should_skip_tag(code_tag, skip_tags):
            continue
        text = code_tag.get_text()
        if _contains_comment(text):
            result.fragments.append(TextFragment(
                text=text, element=code_tag, is_code=True,
            ))

    # Block-level extraction — extract entire block elements (p, h1-h6, li, etc.)
    # instead of individual NavigableStrings. This preserves sentence context
    # and prevents disconnected translations when inline tags split a sentence.
    #
    # Table cells are also extracted individually so their translations
    # stay independent (keeps table integrity).

    # Mark all table cells so they can be skipped on re-parse.
    for table in soup.find_all("table"):
        if not table.get(TRANSLATION_MARKER):
            for cell in table.find_all(["td", "th"]):
                if not cell.get(TRANSLATION_MARKER):
                    cell[TRANSLATION_MARKER] = "1"

    block_elements = []

    for elem in soup.find_all():
        if not hasattr(elem, "name") or elem.name not in BLOCK_TAGS:
            continue
        if elem.get(TRANSLATION_MARKER):
            continue
        if _should_skip_tag(elem, skip_tags):
            continue
        # Skip code blocks and anything inside code blocks
        if elem.name in CODE_TAGS or _within_tags(elem, CODE_TAGS):
            continue
        # Skip if inside a table (cells are extracted separately below)
        if _within_tags(elem, {"table"}):
            continue
        # Skip if any ancestor has the translation marker
        if _has_ancestor_with_marker(elem):
            continue

        if not _is_leaf_block(elem):
            continue
        # Skip table-wrapper elements (e.g., <div class="Table">) that
        # have no direct text — only cell contents should be translated.
        if elem.find("table") is not None:
            direct_text = "".join(
                str(c) for c in elem.children if isinstance(c, NavigableString)
            ).strip()
            if not direct_text:
                continue
        # Skip elements that contain inline code tags (<code>, <pre>)
        if _contains_code(elem):
            continue
        block_elements.append(elem)

    for elem in block_elements:
        text = elem.decode_contents().strip()
        if _is_translatable(text):
            result.fragments.append(TextFragment(text=text, element=elem))

    # ── Mixed-content block elements ────────────────────────────
    # Some EPUBs place text and nested blocks (like <table>) as siblings
    # inside a container div.  The container is not a leaf block, so it
    # was skipped above; but its direct text (NavigableString children)
    # still needs translating.
    for elem in soup.find_all():
        if not hasattr(elem, "name") or elem.name not in BLOCK_TAGS:
            continue
        if elem.get(TRANSLATION_MARKER):
            continue
        if _should_skip_tag(elem, skip_tags):
            continue
        if elem.name in CODE_TAGS or _within_tags(elem, CODE_TAGS):
            continue
        if _within_tags(elem, {"table"}):
            continue
        if _has_ancestor_with_marker(elem):
            continue
        if _is_leaf_block(elem):
            continue  # already handled
        if _contains_code(elem):
            continue

        # Collect all direct NavigableStrings + text from inline children
        # (e.g., <span>, <a>, <i>) into one fragment.  Using the PARENT
        # DIV as the element ensures write_back creates a separate block
        # for the translation, not an inline <span>.
        parts = []
        for child in elem.children:
            if isinstance(child, NavigableString):
                t = str(child).strip()
                if t:
                    parts.append(t)
            elif hasattr(child, "name") and child.name not in BLOCK_TAGS:
                # Inline element (<span>, <a>, <i>, etc.) — include its text
                t = child.get_text(strip=True)
                if t:
                    parts.append(t)
        direct_text = " ".join(parts)
        if _is_translatable(direct_text):
            result.fragments.append(TextFragment(text=direct_text, element=elem))

    # Table cells: extract as individual fragments.
    # Do NOT require _is_leaf_block — many EPUBs nest <p> inside <td>/<th>.
    # Use get_text() (plain text) instead of decode_contents() so the
    # translator receives clean text without nested HTML tags.
    for cell in soup.find_all(["td", "th"]):
        if _should_skip_tag(cell, skip_tags):
            continue
        if _contains_code(cell):
            continue
        text = cell.get_text(separator="\n", strip=True)
        if _is_translatable(text):
            result.fragments.append(TextFragment(text=text, element=cell))

    return result


def parse_ncx_file(file_path: str, bilingual: bool = False) -> ParsedFile:
    """Parse a toc.ncx file and extract translatable text."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "xml")
    result = ParsedFile(
        file_path=file_path, soup=soup, original_content=content, bilingual=bilingual,
        is_xml=True,
    )

    if bilingual:
        for elem in soup.find_all("text"):
            if elem.get(TRANSLATION_MARKER):
                continue
            text = elem.get_text(strip=True)
            if _is_translatable(text):
                result.fragments.append(TextFragment(text=text, element=elem))
    else:
        seen_ids = set()
        for tag_name in ("docTitle", "navLabel", "text"):
            for elem in soup.find_all(tag_name):
                for nav_string in elem.find_all(string=True):
                    if isinstance(nav_string, NavigableString) and _is_translatable(
                        str(nav_string)
                    ):
                        nsid = id(nav_string)
                        if nsid not in seen_ids:
                            seen_ids.add(nsid)
                            result.fragments.append(
                                TextFragment(text=str(nav_string), element=nav_string)
                            )

    return result


def parse_opf_file(file_path: str, bilingual: bool = False) -> ParsedFile:
    """Parse content.opf and extract translatable metadata."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "xml")
    result = ParsedFile(
        file_path=file_path, soup=soup, original_content=content, bilingual=bilingual,
        is_xml=True,
    )

    if bilingual:
        for tag_name in ("title", "creator", "description"):
            for elem in soup.find_all(tag_name):
                if elem.get(TRANSLATION_MARKER):
                    continue
                text = elem.get_text(strip=True)
                if _is_translatable(text):
                    result.fragments.append(TextFragment(text=text, element=elem))
    else:
        seen_ids = set()
        for tag_name in ("title", "creator", "description"):
            for elem in soup.find_all(tag_name):
                for nav_string in elem.find_all(string=True):
                    if isinstance(nav_string, NavigableString) and _is_translatable(
                        str(nav_string)
                    ):
                        nsid = id(nav_string)
                        if nsid not in seen_ids:
                            seen_ids.add(nsid)
                            result.fragments.append(
                                TextFragment(text=str(nav_string), element=nav_string)
                            )

    return result


def parse_file(
    file_path: str, skip_tags: list[str], bilingual: bool = False
) -> ParsedFile:
    """Parse any file type and extract translatable fragments."""
    basename = os.path.basename(file_path).lower()
    if basename in ("toc.ncx",) or file_path.lower().endswith(".ncx"):
        return parse_ncx_file(file_path, bilingual=bilingual)
    if basename in ("content.opf",) or file_path.lower().endswith(".opf"):
        return parse_opf_file(file_path, bilingual=bilingual)
    if file_path.lower().endswith((".html", ".htm", ".xhtml", ".xml")):
        return parse_html_file(file_path, skip_tags, bilingual=bilingual)
    return ParsedFile(file_path=file_path, soup=BeautifulSoup("", "lxml"))
