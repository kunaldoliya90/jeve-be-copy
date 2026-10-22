"""Thresholds and limits the harness decides by. Anything per-environment is a setting."""

from enum import StrEnum
from typing import Final


class WorkbookFormat(StrEnum):
    """Formats the gatekeeper accepts, and what each costs to read."""

    XLSX = ".xlsx"
    XLSM = ".xlsm"
    XLSB = ".xlsb"
    """Binary. Values only: pyxlsb exposes no formulas, so lineage and tie-out degrade."""


ACCEPTED_FORMATS: Final = frozenset(WorkbookFormat)

ZIP_MAGIC: Final = b"PK\x03\x04"
"""An OOXML package is a zip. The first bytes are trusted; the extension and MIME are not."""

REQUIRED_ZIP_MEMBERS: Final = ("[Content_Types].xml", "xl/workbook.xml")
"""Present in every .xlsx and .xlsm. Their absence means a zip that is not a workbook."""

MAX_ZIP_MEMBERS: Final = 2_000
MAX_UNCOMPRESSED_BYTES: Final = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO: Final = 100
"""Zip-bomb guards.

The ratio is the real signal: a bomb expands by thousands of times, while a legitimate
binary workbook of 37,613 rows expands 11 MB to 238 MB — a ratio of 20. The absolute cap is
the memory backstop behind it, set high enough that a genuine workbook at the upload limit
still passes.
"""

MIN_HEADER_COLUMNS: Final = 3
"""Fewer labelled columns than this is a title block, not a table."""

HEADER_TEXT_SHARE: Final = 0.6
"""A header row is at least this share text labels."""

MIN_TYPED_OVER_FORMULAS: Final = 2
"""Formulas a column needs before a constant in it reads as an overwrite rather than
as an ordinary entry. The column must also hold more formulas than constants."""

CATEGORY_MAX_DISTINCT: Final = 50
"""Above this a text column is free text, and no sample of it reaches the model."""

FIGURE_SAMPLE_CAP: Final = 10
"""Exemplars carried on a figure, when no deployment setting says otherwise.

A review run by the orchestrator uses ``config.FIGURE_SAMPLES`` instead. Exceptions are
counted and priced in full either way; only the examples shown to a reader are capped,
worst first."""

CATEGORY_SAMPLES: Final = 3
"""Sample values per short category column: codes, currencies, periods. Never amounts."""

MAX_HEADERS_WHEN_TRIMMED: Final = 12
"""Header labels kept per sheet once the structure context has to be trimmed.

The last tier of trimming drops detail, never a sheet: AI-1 assigns a role to every
sheet it is shown, so one it never sees is a tab that silently has no role."""

BLANK_RUN_LIMIT: Final = 500
"""Consecutive empty rows that end a sheet. Guards against a stray cell at row 1,048,576."""

CHARS_PER_TOKEN: Final = 4
"""Rough token estimate for the structure-context cap. Trimming is approximate by design."""

SHORT_DIGEST_LENGTH: Final = 12
"""Digest characters shown to a person: enough to tell two templates apart in a list."""
