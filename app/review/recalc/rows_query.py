"""Paging, filtering, sorting and grouping over a stored recalculation's full row set.

Pure functions over a plain list, on purpose: the whole set fits comfortably in memory at
this system's real scale (the widest sample workbook is 37,613 rows), so there is nothing a
database buys here that a sort and a slice do not. If a JE type ever needs to browse rows at
a scale this stops being true for, that is the point to reach for one - not before.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final, cast

from review.models.recalculation import DiscrepancyKind, RecalcRow, RowFlag

DEFAULT_LIMIT: Final = 100
MAX_LIMIT: Final = 500

_STATUS_CHANGED: Final = "changed"
_STATUS_AGREES: Final = "agrees"
_STATUS_ALL: Final = "all"


def _flagged(row: RecalcRow) -> bool:
    """Whether a row carries a finding, from this check or another one reading the table."""
    return row.kind is not None or bool(row.flags)


@dataclass(frozen=True, slots=True)
class RowPage:
    """One page of rows, and whether there is another."""

    rows: tuple[RecalcRow, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """One grouping dimension's bucket, rolled up from the rows inside it."""

    value: str
    rows: int
    flagged: int
    recomputed_total: Decimal
    stated_total: Decimal
    difference: Decimal = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "difference", self.recomputed_total - self.stated_total)


def filter_rows(rows: Sequence[RecalcRow], *, status: str | None) -> list[RecalcRow]:
    """``changed`` is every row with a finding - this check's own, or another check's on the
    same table, merged in by ``merge_related_flags``; ``agrees`` is every row without one.

    A specific ``DiscrepancyKind`` value narrows further, to one kind of this check's own
    finding - the data gaps apart from the amount differences, say; a related check's flags
    are not a ``DiscrepancyKind`` and are not reachable through this narrower form.
    ``None`` and ``all`` are the same request.
    """
    if status is None or status == _STATUS_ALL:
        return list(rows)
    if status == _STATUS_CHANGED:
        return [row for row in rows if _flagged(row)]
    if status == _STATUS_AGREES:
        return [row for row in rows if not _flagged(row)]
    try:
        kind = DiscrepancyKind(status)
    except ValueError:
        return []
    return [row for row in rows if row.kind is kind]


def sort_rows(rows: list[RecalcRow], *, sort: str | None) -> list[RecalcRow]:
    """``-difference`` is the default a reviewer wants: the worst rows first.

    Missing values sort last regardless of direction - a row with nothing to rank by is not
    the same as one ranked at zero, and burying it at the bottom either way keeps a reviewer
    from reading an absence as a clean result.
    """
    if not sort:
        return rows
    descending = sort.startswith("-")
    field_name = sort[1:] if descending else sort
    if field_name not in {"row", "recomputed", "stated", "difference", "difference_pct"}:
        return rows

    def magnitude(row: RecalcRow) -> Decimal | float | int:
        value = cast("Decimal | float | int", getattr(row, field_name))
        if field_name == "difference":
            return value if value >= 0 else -value
        return value

    # Split rather than fold "has no value" into the sort key: a single `reverse` flag
    # flips direction and "comes last" together, which is what put a missing value first
    # on a descending sort. Splitting keeps "last, always" independent of direction.
    present = [r for r in rows if getattr(r, field_name) is not None]
    missing = [r for r in rows if getattr(r, field_name) is None]
    present.sort(key=magnitude, reverse=descending)
    return [*present, *missing]


def paginate(rows: Sequence[RecalcRow], *, limit: int, cursor: str | None) -> RowPage:
    """An offset cursor, not an opaque one: the whole set is re-derived fresh each request
    from an immutable file, so there is nothing for an opaque cursor to protect against."""
    start = _offset(cursor)
    bounded = max(1, min(limit, MAX_LIMIT))
    page = rows[start : start + bounded]
    end = start + len(page)
    next_cursor = str(end) if end < len(rows) else None
    return RowPage(rows=tuple(page), total=len(rows), next_cursor=next_cursor)


def group_rows(rows: Sequence[RecalcRow], *, dimension: str) -> list[GroupSummary]:
    """One summary per distinct value of ``dimension``, sorted by the value itself.

    A row that never bound the dimension groups under the empty string rather than being
    dropped - "no cost centre on this row" is itself something a reviewer can act on.
    """
    buckets: dict[str, list[RecalcRow]] = {}
    for row in rows:
        buckets.setdefault(row.group.get(dimension, ""), []).append(row)

    return [
        GroupSummary(
            value=value,
            rows=len(bucket),
            flagged=sum(1 for row in bucket if _flagged(row)),
            recomputed_total=sum((row.recomputed or Decimal(0) for row in bucket), Decimal(0)),
            stated_total=sum((row.stated or Decimal(0) for row in bucket), Decimal(0)),
        )
        for value, bucket in sorted(buckets.items())
    ]


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError:
        return 0
    return max(0, value)


@dataclass(frozen=True, slots=True)
class RelatedCheck:
    """One other check's own (already worst-first, capped) samples, keyed by row number.

    ``samples`` is a tool's figure samples verbatim - dicts with ``row``, ``cell``,
    ``expected``, ``actual`` and ``difference`` keys, the shape every reperform-based check
    already produces for its own citations. Nothing here re-derives or re-judges them.
    """

    check_id: str
    label: str
    samples: tuple[dict[str, Any], ...]


def merge_related_flags(
    rows: Sequence[RecalcRow], related: Sequence[RelatedCheck]
) -> list[RecalcRow]:
    """Annotate each row with citations from other checks reading the same table.

    A row this does not touch may still differ on a related check if that check's own
    citation list was capped before reaching this row - the related check's own full count
    is carried on ``RelatedRecalcCheck.citation_count``, not reconstructed here.
    """
    by_row: dict[int, list[RowFlag]] = {}
    for check in related:
        for sample in check.samples:
            row_no = sample.get("row")
            if not isinstance(row_no, int):
                continue
            expected = sample.get("expected")
            actual = sample.get("actual")
            by_row.setdefault(row_no, []).append(
                RowFlag(
                    check_id=check.check_id,
                    label=check.label,
                    cell=str(sample.get("cell") or ""),
                    expected=expected,
                    actual=actual,
                    difference=sample.get("difference"),
                    why=(
                        f"{check.label}: expected {expected}, the workbook states {actual}."
                        if expected is not None and actual is not None
                        else f"Also flagged by {check.label}."
                    ),
                )
            )
    if not by_row:
        return list(rows)
    return [
        row.model_copy(update={"flags": tuple(by_row[row.row])}) if row.row in by_row else row
        for row in rows
    ]


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "GroupSummary",
    "RelatedCheck",
    "RowPage",
    "filter_rows",
    "group_rows",
    "merge_related_flags",
    "paginate",
    "sort_rows",
]
