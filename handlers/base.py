"""Abstract base class for file format handlers.

Every supported file format implements this interface.
The server dispatches to the appropriate handler based on file extension.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TextFragment:
    """A translatable text unit extracted from a document.

    Attributes:
        text: The source text to translate
        meta: Arbitrary metadata (handler-specific, used during rebuild)
    """
    text: str
    meta: dict = field(default_factory=dict)


class BaseHandler(ABC):
    """Interface that all file format handlers must implement."""

    @staticmethod
    def supported_extensions() -> list[str]:
        """Return list of supported file extensions (lowercase, with dot).
        E.g. ['.epub'] or ['.docx']
        """
        raise NotImplementedError

    @abstractmethod
    def extract(self, file_path: str, skip_tags: list[str] = None,
                bilingual: bool = True, **kwargs) -> list[TextFragment]:
        """Extract translatable fragments from the file.

        Args:
            file_path: Path to the source file
            skip_tags: Tags/elements to skip (handler interprets as needed)
            bilingual: Whether bilingual mode is enabled
            **kwargs: Extra handler-specific options (e.g. pages for PDF)

        Returns:
            List of TextFragment with text and metadata for rebuild
        """

    @abstractmethod
    def rebuild(self, file_path: str, fragments: list[TextFragment],
                translations: list[str], bilingual: bool,
                target_lang: str = "zh-CN", **kwargs) -> str:
        """Write translations back and produce the output file.

        Args:
            file_path: Original file path
            fragments: The fragments returned by extract()
            translations: Parallel list of translated texts
            bilingual: Whether to keep original + add translation
            target_lang: Target language code for lang attributes (EPUB only)
            **kwargs: Extra handler-specific options (e.g. method for PDF)

        Returns:
            Path to the output file
        """
