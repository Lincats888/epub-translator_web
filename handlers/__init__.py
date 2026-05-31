"""Handler registry — maps file extensions to handler classes.

Usage:
    from handlers import get_handler
    handler = get_handler("book.epub")  # returns EpubHandler instance
    handler = get_handler("doc.docx")   # returns DocxHandler instance
"""

import os
from typing import Dict, Type, Optional, List

from .base import BaseHandler
from .epub_handler import EpubHandler
from .docx_handler import DocxHandler
from .pdf_handler import PdfHandler

# Build registry: extension -> handler class
_REGISTRY = {}  # type: Dict[str, Type[BaseHandler]]
for _handler_cls in [EpubHandler, DocxHandler, PdfHandler]:
    for _ext in _handler_cls.supported_extensions():
        _REGISTRY[_ext.lower()] = _handler_cls


def get_handler(filename):
    # type: (str) -> Optional[BaseHandler]
    """Get a handler instance for the given filename."""
    ext = os.path.splitext(filename)[1].lower()
    cls = _REGISTRY.get(ext)
    return cls() if cls else None


def get_supported_extensions():
    # type: () -> List[str]
    """Return all supported file extensions."""
    return list(_REGISTRY.keys())


def is_supported(filename):
    # type: (str) -> bool
    """Check if a filename has a supported extension."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in _REGISTRY
