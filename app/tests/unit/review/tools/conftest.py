"""A tiny in-memory workbook and a bound sheet map, so tool tests read like the real thing."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest

from api.consts import RiskRating
from review.consts import FIGURE_SAMPLE_CAP
from review.models.context import Band, BusinessRules
from review.models.sheet_map import SheetMap
from review.models.verdicts import Status
from review.models.workbook import TableRef
from review.models.workbook_data import Cell, CellValue, Sheet, WorkbookData
from review.tools.base import ToolContext
from review.utils.sheet_utils import column_letter

RULES = BusinessRules(
    version=1,
    credits={
        Status.PASS: 100,
        Status.PARTIAL: 50,
        Status.UNABLE_TO_VALIDATE: 50,
        Status.MISSING_EVIDENCE: 0,
        Status.FAIL: 0,
    },
    bands=(
        Band(min=51, label="Ok", risk=RiskRating.LOW),
        Band(min=0, label="Risk", risk=RiskRating.HIGH),
    ),
)


def build_sheet(name: str, rows: Sequence[Sequence[CellValue]]) -> Sheet:
    """Row 1 is the header; data follows. Blank cells are simply absent, as in a real file."""
    cells: dict[tuple[int, int], Cell] = {}
    width = max((len(row) for row in rows), default=0)
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row, start=1):
            if value is not None:
                cells[(row_index, column_index)] = Cell(value=value)
    return Sheet(name=name, max_row=len(rows), max_column=width, cells=cells)


def table_ref(sheet: str, headers: Sequence[str], last_row: int) -> TableRef:
    return TableRef(
        sheet=sheet,
        header_row=1,
        first_row=2,
        last_row=last_row,
        columns={meaning: column_letter(index) for index, meaning in enumerate(headers, start=1)},
    )


SheetSpec = tuple[Sequence[str], Sequence[Sequence[CellValue]]]


def make_context(
    sheets: dict[str, SheetSpec],
    facts: dict[str, Any] | None = None,
    figure_samples: int = FIGURE_SAMPLE_CAP,
) -> ToolContext:
    """Build a tool context from ``{table_name: (column meanings, data rows)}``."""
    built: list[Sheet] = []
    tables: dict[str, TableRef] = {}
    for name, (headers, rows) in sheets.items():
        header_row: list[CellValue] = list(headers)
        all_rows: list[Sequence[CellValue]] = [header_row, *(list(row) for row in rows)]
        built.append(build_sheet(name, all_rows))
        tables[name] = table_ref(name, headers, last_row=len(all_rows))
    return ToolContext(
        data=WorkbookData(sheets=tuple(built)),
        sheet_map=SheetMap(tables=tables),
        rules=RULES,
        facts=facts or {},
        figure_samples=figure_samples,
    )


@pytest.fixture
def balanced_posting() -> ToolContext:
    """Two company codes, each netting to zero, with a liability credit against expenses."""
    return make_context(
        {
            "je_posting": (
                ("company_code", "account", "currency", "line_amount"),
                [
                    ["FR01", "720008", "EUR", Decimal("1200.50")],
                    ["FR01", "322000", "EUR", Decimal("-1200.50")],
                    ["FR02", "720008", "EUR", Decimal("880.00")],
                    ["FR02", "322000", "EUR", Decimal("-880.00")],
                ],
            )
        },
        facts={"hash_total": Decimal("4161.00"), "je_total": Decimal("0")},
    )
