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
                    _append_html(cloned, translation, self.soup)
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
    """Check if this element or any descendant is a code tag (<code> or <pre>)
    or uses code-related CSS classes (common in EPUB code blocks)."""
    if hasattr(tag, "name") and tag.name in ("code", "pre"):
        return True
    for child in tag.find_all(["code", "pre"]):
        return True
    # Detect code blocks by CSS class names used in EPUBs
    # class_sch = code line, class_skus = code text, class_scn = code separator
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


def _is_leaf_block(elem: Tag) -> bool:
    """True if this block element doesn't contain other block-level elements."""
    for child in elem.find_all():
        if child is elem:
            continue
        if hasattr(child, "name") and child.name in BLOCK_TAGS:
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
        # Skip elements that contain inline code tags (<code>, <pre>)
        if _contains_code(elem):
            continue
        block_elements.append(elem)

    for elem in block_elements:
        text = elem.decode_contents().strip()
        if _is_translatable(text):
            result.fragments.append(TextFragment(text=text, element=elem))

    # Table cells: extract as individual fragments
    for cell in soup.find_all(["td", "th"]):
        if _should_skip_tag(cell, skip_tags):
            continue
        if _contains_code(cell):
            continue
        if not _is_leaf_block(cell):
            continue
        text = cell.decode_contents().strip()
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
