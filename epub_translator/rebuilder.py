import os
import zipfile

# CSS injected after translation to improve Chinese text readability.
# Chinese glyphs are taller than Latin, so the default line-height (~1.2)
# feels cramped. Boosting it to 1.8 makes bilingual content much more readable.
_LINE_HEIGHT_CSS = """
/* EpubTranslator: boosted line-height for Chinese readability */
body, p, div, li, td, th, dt, dd, blockquote {
    line-height: 1.8;
}
h1, h2, h3, h4, h5, h6 {
    line-height: 1.5;
}
/* EpubTranslator: table borders */
table {
    border-collapse: collapse;
    width: 100%;
}
th, td {
    border: 1px solid #999;
    padding: 6px 10px;
    text-align: left;
}
th {
    background-color: #f0f0f0;
}
"""


def inject_line_height(extract_dir: str):
    """Append line-height CSS rules to every stylesheet in the extracted EPUB."""
    for root, _dirs, files in os.walk(extract_dir):
        for filename in files:
            if filename.lower().endswith(".css"):
                css_path = os.path.join(root, filename)
                with open(css_path, "a", encoding="utf-8") as f:
                    f.write(_LINE_HEIGHT_CSS)


def rebuild_epub(extract_dir: str, output_dir: str, book_name: str) -> str:
    """Rebuild an EPUB file from the translated contents in extract_dir.

    Ensures the mimetype file is first in the archive and stored uncompressed,
    as required by the EPUB specification.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{book_name}_zh.epub")

    # Collect all files from the extracted directory
    file_list = []
    for root, dirs, files in os.walk(extract_dir):
        for filename in files:
            full_path = os.path.join(root, filename)
            arcname = os.path.relpath(full_path, extract_dir).replace("\\", "/")
            file_list.append((full_path, arcname))

    # mimetype must be first and STORED (no compression)
    mimetype_src = os.path.join(extract_dir, "mimetype")
    if not os.path.exists(mimetype_src):
        raise FileNotFoundError(
            f"mimetype file not found in {extract_dir}. Corrupted EPUB extraction."
        )

    # Sort: mimetype first, then everything else in sorted order
    def sort_key(item):
        _, arcname = item
        if arcname == "mimetype":
            return (0, "")
        return (1, arcname)

    file_list.sort(key=sort_key)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full_path, arcname in file_list:
            compress = zipfile.ZIP_STORED if arcname == "mimetype" else zipfile.ZIP_DEFLATED
            zf.write(full_path, arcname, compress_type=compress)

    return output_path
