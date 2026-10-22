"""A real .xlsx built in memory, so the reader and profiler are tested on a workbook.

Shaped like the entries the harness reviews: a header block above the table, a formula
column that links to another sheet, one typed-over number and one hidden sheet.
"""

import re
import zipfile
from io import BytesIO

import openpyxl
import pytest


def _with_cached_values(content: bytes, cached: dict[str, str]) -> bytes:
    """Add the ``<v>`` Excel writes beside every ``<f>``, which openpyxl does not.

    Without this the fixture cannot exercise the two-pass read at all: a formula cell would
    carry no value, and a workbook saved by Excel always carries both.
    """
    buffer = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(content)) as source,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                text = payload.decode("utf-8")
                for ref, value in cached.items():
                    text = re.sub(
                        rf'(<c r="{ref}"[^>]*>)(<f[^>]*>.*?</f>)',
                        rf"\1\2<v>{value}</v>",
                        text,
                    )
                payload = text.encode("utf-8")
            target.writestr(item, payload)
    return buffer.getvalue()


@pytest.fixture
def workbook_bytes() -> bytes:
    """A small two-sheet workbook with a linked amount column and one typed-over cell."""
    workbook = openpyxl.Workbook()

    source = workbook.active
    assert source is not None
    source.title = "ADP GL"
    source.append(["Company", "Currency", "Amount"])
    for index, (company, amount) in enumerate(
        [("FR01", "1200.50"), ("FR01", "880.00"), ("FR02", "2415.75")], start=2
    ):
        source.cell(row=index, column=1, value=company)
        source.cell(row=index, column=2, value="EUR")
        source.cell(row=index, column=3, value=float(amount))

    posting = workbook.create_sheet("JE Upload")
    posting["A1"] = "Vacation accrual - February 2026"
    posting.append([])
    posting.append(["Company", "Account", "Currency", "Debit"])
    for index, company in enumerate(["FR01", "FR01", "FR02"], start=4):
        posting.cell(row=index, column=1, value=company)
        posting.cell(row=index, column=2, value="720008")
        posting.cell(row=index, column=3, value="EUR")
        posting.cell(row=index, column=4, value=f"='ADP GL'!C{index - 2}")
    # The preparer typed over the last line rather than linking it.
    posting.cell(row=6, column=4, value=2415.75)

    hidden = workbook.create_sheet("Working")
    hidden.sheet_state = "hidden"
    hidden.append(["Note", "Value"])
    hidden.append(["rate", 0.2])

    buffer = BytesIO()
    workbook.save(buffer)
    return _with_cached_values(buffer.getvalue(), {"D4": "1200.5", "D5": "880"})
