"""Step 6. Reduce the profile to the only view of the workbook AI-1 ever sees.

Two rules shape what goes in. **Shape, not values**: sheet names, row counts, header labels,
column types and exact column totals, plus a few sample values from short category columns
(codes, currencies, periods) — never an amount, never free text, never an employee. And
**bounded**: if the result exceeds the cap, samples are dropped first, then tables beyond the
first few per sheet are summarised to one line, and the context records that it was trimmed.

The layout fingerprint is taken here too. It deliberately excludes row counts, values and
dates, so next month's longer file from the same template fingerprints identically — which
is what lets step 7 skip AI-1 and AI-2 entirely.
"""

import hashlib
import logging

from review.consts import (
    CATEGORY_MAX_DISTINCT,
    CATEGORY_SAMPLES,
    MAX_HEADERS_WHEN_TRIMMED,
)
from review.models.workbook import (
    ColumnProfile,
    ColumnType,
    SheetSummary,
    StructureContext,
    TableProfile,
    WorkbookProfile,
)
from review.models.workbook_data import CellValue, WorkbookData
from review.utils.sheet_utils import SheetUtils, column_index, normalize_header

logger = logging.getLogger(__name__)

_SEPARATOR = "|"


def build_structure_context(
    profile: WorkbookProfile, data: WorkbookData, *, max_tokens: int
) -> StructureContext:
    """The bounded, value-free description of this workbook."""
    summaries = [_sheet_summary(profile, data, sheet.name) for sheet in profile.sheets]
    fingerprint = layout_fingerprint(profile)
    lineage = _lineage(profile)
    defects = _defects_summary(profile)

    context = StructureContext(
        sheets=tuple(summaries),
        lineage=lineage,
        defects_summary=defects,
        layout_fingerprint=fingerprint,
        token_estimate=0,
        trimmed=False,
    )
    return _fit(context, max_tokens)


def layout_fingerprint(profile: WorkbookProfile) -> str:
    """A stable id for the *template*, not the file.

    Over sorted sheet names, each table's normalised header texts with its column types, and
    the R1C1 formula shapes. Row counts and every value are excluded on purpose: those are
    exactly what changes between periods while the layout does not.
    """
    parts: list[str] = sorted(
        normalize_header(sheet.name) for sheet in profile.sheets if sheet.name
    )
    for table in sorted(profile.tables, key=lambda t: (t.sheet, t.header_row)):
        headers = _SEPARATOR.join(normalize_header(col.header) for col in table.columns)
        types = _SEPARATOR.join(col.column_type for col in table.columns)
        parts.append(f"{normalize_header(table.sheet)}>{headers}>{types}")
    # Distinct shapes only. The same filled-down formula breaks into a different number of
    # runs between periods whenever a preparer types over a cell, so counting runs would make
    # the fingerprint depend on the data it is supposed to ignore.
    parts.extend(
        sorted(
            {
                f"{normalize_header(shape.sheet)}>{shape.column}>{shape.r1c1}"
                for shape in profile.formula_shapes
            }
        )
    )
    return hashlib.sha256(_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


def _sheet_summary(profile: WorkbookProfile, data: WorkbookData, name: str) -> SheetSummary:
    sheet = next(s for s in profile.sheets if s.name == name)
    tables = [table for table in profile.tables if table.sheet == name]
    columns = tables[0].columns if tables else ()

    return SheetSummary(
        name=name,
        hidden=sheet.hidden,
        row_count=sheet.row_count,
        headers=tuple(column.header for column in columns),
        column_types=tuple(column.column_type for column in columns),
        totals={col.letter: col.total for col in columns if col.total is not None},
        samples=_samples(tables, data, name),
    )


def _samples(
    tables: list[TableProfile], data: WorkbookData, sheet_name: str
) -> dict[str, tuple[str, ...]]:
    """A few distinct values from short text columns only.

    A column with many distinct values is free text or an identifier; neither tells AI-1
    anything about the layout, and both risk carrying personal data into a prompt.
    """
    sheet = data.sheet(sheet_name)
    if sheet is None or not tables:
        return {}

    table = tables[0]
    samples: dict[str, tuple[str, ...]] = {}
    for column in table.columns:
        if not _is_category(column):
            continue
        index = column_index(column.letter)
        seen: list[str] = []
        for row in range(table.first_row, table.last_row + 1):
            value = sheet.cell(row, index).value
            text = _as_text(value)
            if text and text not in seen:
                seen.append(text)
            if len(seen) >= CATEGORY_SAMPLES:
                break
        if seen:
            samples[column.letter] = tuple(seen)
    return samples


def _is_category(column: ColumnProfile) -> bool:
    return (
        column.column_type is ColumnType.TEXT
        and column.distinct_count is not None
        and 0 < column.distinct_count <= CATEGORY_MAX_DISTINCT
    )


def _as_text(value: CellValue) -> str:
    return value.strip() if isinstance(value, str) else ""


def _lineage(profile: WorkbookProfile) -> tuple[str, ...]:
    """Readable feeds-from edges, deduplicated: which sheet a column's formulas read."""
    edges = {
        f"{shape.sheet}!{shape.column} <- {source}"
        for shape in profile.formula_shapes
        for source in shape.feeds_from
        if source != shape.sheet
    }
    return tuple(sorted(edges))


def _defects_summary(profile: WorkbookProfile) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for defect in profile.defects:
        counts[defect.kind] = counts.get(defect.kind, 0) + 1
    return tuple(f"{kind}: {count}" for kind, count in sorted(counts.items()))


def _fit(context: StructureContext, max_tokens: int) -> StructureContext:
    """Measure, then trim in the agreed order until it fits: samples, then extra tables."""
    estimate = _estimate(context)
    if estimate <= max_tokens:
        return context.model_copy(update={"token_estimate": estimate})

    without_samples = context.model_copy(
        update={"sheets": tuple(s.model_copy(update={"samples": {}}) for s in context.sheets)}
    )
    estimate = _estimate(without_samples)
    if estimate <= max_tokens:
        logger.info("structure context trimmed: samples dropped tokens=%d", estimate)
        return without_samples.model_copy(update={"token_estimate": estimate, "trimmed": True})

    # Never drop a sheet. AI-1 assigns a role to every sheet it is shown, and one it was
    # never shown simply has no role — the quietest way to lose a tab. Detail goes instead.
    capped = without_samples.model_copy(
        update={
            "sheets": tuple(
                sheet.model_copy(
                    update={
                        "headers": sheet.headers[:MAX_HEADERS_WHEN_TRIMMED],
                        "column_types": sheet.column_types[:MAX_HEADERS_WHEN_TRIMMED],
                        "totals": {},
                    }
                )
                for sheet in without_samples.sheets
            )
        }
    )
    estimate = _estimate(capped)
    if estimate > max_tokens:
        logger.warning(
            "structure context still over cap after trimming tokens=%d limit=%d",
            estimate,
            max_tokens,
        )
    return capped.model_copy(update={"token_estimate": estimate, "trimmed": True})


def _estimate(context: StructureContext) -> int:
    return SheetUtils.estimate_tokens(context.model_dump_json(exclude={"token_estimate"}))
