import hashlib
import os
import shutil
import zipfile
from xml.etree import ElementTree


class EpubExtractor:
    def __init__(self, epub_path: str, temp_dir: str = "temp"):
        self._epub_path = epub_path
        self._book_name = os.path.splitext(os.path.basename(epub_path))[0].strip()
        self._extract_dir = os.path.join(temp_dir, self._book_name)

    @property
    def extract_dir(self) -> str:
        return self._extract_dir

    def _compute_source_hash(self) -> str:
        hasher = hashlib.md5()
        with open(self._epub_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _hash_file(self) -> str:
        hash_path = os.path.join(self._extract_dir, ".source_hash")
        if os.path.exists(hash_path):
            with open(hash_path, "r") as f:
                return f.read().strip()
        return ""

    def _save_hash(self, hash_value: str):
        hash_path = os.path.join(self._extract_dir, ".source_hash")
        with open(hash_path, "w") as f:
            f.write(hash_value)

    def is_extracted(self) -> bool:
        if not os.path.isdir(self._extract_dir):
            return False
        required = ["mimetype", "META-INF/container.xml"]
        for path in required:
            full = os.path.join(self._extract_dir, path)
            if not os.path.exists(full):
                return False
        return self._hash_file() == self._compute_source_hash()

    def extract(self) -> str:
        if self.is_extracted():
            return self._extract_dir

        if os.path.exists(self._extract_dir):
            shutil.rmtree(self._extract_dir)
        os.makedirs(self._extract_dir, exist_ok=True)

        with zipfile.ZipFile(self._epub_path, "r") as zf:
            zf.extractall(self._extract_dir)

        self._save_hash(self._compute_source_hash())
        return self._extract_dir

    def get_opf_path(self) -> str:
        container_path = os.path.join(self._extract_dir, "META-INF", "container.xml")
        if not os.path.exists(container_path):
            raise FileNotFoundError(f"container.xml not found: {container_path}")
        tree = ElementTree.parse(container_path)
        ns = {"ns": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = tree.find(".//ns:rootfile", ns)
        if rootfile is None:
            rootfile = tree.find(".//rootfile")
        if rootfile is None:
            raise ValueError("No rootfile found in container.xml")
        opf_rel = rootfile.get("full-path")
        return os.path.join(self._extract_dir, opf_rel)

    def get_opf_dir(self) -> str:
        return os.path.dirname(self.get_opf_path())

    def list_content_files(self) -> list[str]:
        """Return all HTML/XHTML files referenced in the OPF manifest."""
        opf_path = self.get_opf_path()
        tree = ElementTree.parse(opf_path)
        files = []
        for item in tree.iter():
            # Match <item> elements regardless of namespace
            tag = item.tag.split("}")[-1] if "}" in item.tag else item.tag
            if tag != "item":
                continue
            href = item.get("href")
            media_type = item.get("media-type", "")
            if href and media_type in (
                "application/xhtml+xml",
                "text/html",
                "application/x-dtbncx+xml",
            ):
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(opf_path), href)
                )
                if os.path.exists(resolved):
                    files.append(resolved)
        return files

    def find_toc_file(self) -> str | None:
        """Find the NCX toc file path."""
        opf_path = self.get_opf_path()
        opf_dir = os.path.dirname(opf_path)
        tree = ElementTree.parse(opf_path)

        for item in tree.iter():
            tag = item.tag.split("}")[-1] if "}" in item.tag else item.tag
            if tag != "item":
                continue
            media_type = item.get("media-type", "")
            if media_type == "application/x-dtbncx+xml":
                href = item.get("href")
                if href:
                    return os.path.normpath(os.path.join(opf_dir, href))

        ncx_path = os.path.join(opf_dir, "toc.ncx")
        if os.path.exists(ncx_path):
            return ncx_path
        return None
