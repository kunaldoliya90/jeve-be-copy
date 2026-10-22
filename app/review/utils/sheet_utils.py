"""Generic spreadsheet helpers: column letters, header normalising, token estimates."""

import re
from typing import Final

from review.consts import CHARS_PER_TOKEN

_ALPHABET_SIZE: Final = 26
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


class SheetUtils:
    """Stateless helpers shared by the extract and tools layers."""

    @staticmethod
    def column_letter(index: int) -> str:
        """1 -> ``A``, 27 -> ``AA``. Raises below 1, which is always a caller bug."""
        if index < 1:
            raise ValueError(f"Column index must be 1 or more, got {index}")
        letters = ""
        while index > 0:
            index, remainder = divmod(index - 1, _ALPHABET_SIZE)
            letters = chr(ord("A") + remainder) + letters
        return letters

    @staticmethod
    def column_index(letter: str) -> int:
        """``A`` -> 1, ``AA`` -> 27. The inverse of ``column_letter``."""
        if not letter or not letter.isalpha():
            raise ValueError(f"Column letter must be alphabetic, got {letter!r}")
        index = 0
        for char in letter.upper():
            index = index * _ALPHABET_SIZE + (ord(char) - ord("A") + 1)
        return index

    @staticmethod
    def normalize_header(value: object) -> str:
        """Fold a header to its comparable form.

        Case, punctuation, accents of spacing and trailing footnote markers all differ
        between periods and between entities without the column meaning anything different.
        What survives is letters and digits, single-spaced.
        """
        if value is None:
            return ""
        text = str(value).strip().lower()
        return _NON_ALPHANUMERIC.sub(" ", text).strip()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count for the structure-context cap. Approximate by design."""
        return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


column_letter = SheetUtils.column_letter
column_index = SheetUtils.column_index
normalize_header = SheetUtils.normalize_header
