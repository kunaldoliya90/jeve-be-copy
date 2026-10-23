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


# --- the category split work paper (orchestrator/split_summary.py) ----------------------

MAX_SPLIT_CATEGORIES: Final = 12
"""Classes a column may hold before it is not a classification at all.

A split by something with forty values is a pivot table, not a work paper, and a column
that wide is what the shape fallback would otherwise pick when no meaning is bound."""

MAX_SPLIT_COMPONENTS: Final = 40
"""Measured columns carried on the work paper, in the file's own column order.

Set above the widest sample working (33 quantity columns) on purpose: the cap is here to stop
a pathological sheet, not to edit a real one. When it does bite, a column a formula computes
is kept ahead of a larger one that was typed - the derived columns *are* the calculation, and
ranking on magnitude alone dropped the two day-count columns that carry the leave arithmetic
while keeping four restatements of the same provision."""

MAX_SPLIT_WALKTHROUGHS: Final = 6
"""Rows traced step by step: the first row of the working, then the worst exceptions."""

MAX_WALK_CONTEXT: Final = 4
"""Text columns shown beside a traced row - a cost centre, a currency. Never an identifier."""

IDENTIFIER_DISTINCT_SHARE: Final = 0.9
"""Above this share of distinct values a column identifies the row rather than describing it.

The second of two guards on the walkthrough, behind the bound-meaning check. A column
nobody has taught the alias table about still fails closed."""

HIGH_SEVERITY_WEIGHT: Final = 15
"""Area weight at which a failed check is reported as high severity rather than medium.

Read off the weights the business confirmed, so severity on the work paper and weight in
the score can never tell a reader two different stories about what matters."""


CONTROL_TOTAL_MAX_DRIFT: Final = 0.5
"""How far a number above a column may sit from that column's own sum and still be its total.

A control total is identified by being the *same quantity* as the column beneath it. The
header band of a real working also holds rates, counts and per-unit figures, and one of those
differing from the column's sum by five orders of magnitude is a different fact - not a
control total that disagrees. Reporting it as one would be a confident false finding, which
is worse than not finding it. A stale or hardcoded total, the thing this is looking for, is
off by a rounding or by one period's movement and stays well inside the band."""


GENERAL_NUMBER_FORMAT: Final = "General"
"""Excel's default format: the one a preparer gets by typing a number and formatting nothing.

Every real quantity in these workbooks carries an explicit format - an accounting mask or
``#,##0.00`` - because somebody meant it to be read as money or as days. A numeric column
left on the default *and* holding only whole numbers is a code the preparer never intended as
a quantity, and on the sample files it is exactly one column: the SAP company code, whose sum
is 2,633,130 and means nothing. Both conditions are required, so a General column holding a
fraction somewhere is still a quantity."""

FORMULA_SEARCH_ROWS: Final = 200
"""Rows scanned down a column for the formula behind it, when the first data row is typed.

One sample working holds its whole first row as constants, so stopping at the first row would
report a file with 2,136 formulas in it as having no calculation rules at all."""
