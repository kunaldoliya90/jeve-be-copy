"""Record-level re-performance, driven by the identities the business declares.

One tool for every row-level identity a JE type states. It replaced three tools whose
arithmetic was Python - ``recalc_accrual``, ``recalc_leave_balance`` and
``recalc_salary_per_day`` - and the reason for the change is not that the code was wrong: it
is that a business owner could read their own arithmetic only by reading code, and a leave
convention that differs by country meant a new tool rather than a new line of YAML.

What it keeps from those three, because each was a decision somebody made for a reason:

* **A missing input stays missing.** A record where an input or the stated figure does not
  resolve is counted as *unbound*, never as a difference: the workbook claimed nothing
  there, so there is nothing to disagree with.
* **Per-row headroom is declared, not assumed.** A product rounded to the currency's scale
  before it was stored needs a cent of room; a sum of two stored amounts does not.
* **An identity that fails on nearly every row is the wrong identity.** Below the
  ``min_tie_rate`` the business states, it reports as unable to validate and says the
  workbook derives the figure some other way. 2,000 confident false findings are worse than
  an honest "this could not be re-performed".
* **Row coverage travels with the figure**, so a figure that did not cover every record can
  never support a pass. ``Figure.supports_pass`` enforces that, here as everywhere.
"""

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, NamedTuple

from review.models.context import ReperformanceSpec, RowIdentitySpec
from review.models.evidence import Figure, RowCoverage
from review.recalc.formula import compile_formula
from review.tools.base import BaseTool, BoundTable, ToolContext, ToolError, worst_first

logger = logging.getLogger(__name__)

TOOL_NAME = "reperform"


class _RowDiff(NamedTuple):
    row: int
    cell: str
    expected: Decimal
    actual: Decimal
    difference: Decimal

    def as_sample(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "cell": self.cell,
            "expected": self.expected,
            "actual": self.actual,
            "difference": self.difference,
        }


class ReperformTool(BaseTool):
    """Does the arithmetic the business states hold on every record?

    The identities are named by the recipe and read from the entry type, never from the
    plan: an identity arriving as a plan argument could be chosen by whatever wrote the
    plan, and how an arithmetic check is performed must never be chosen by the system that
    interprets the result.
    """

    name = TOOL_NAME
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "Table holding the records"},
        "identities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ids of the identities this check re-performs, from the entry type",
        },
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "",
        identities: Sequence[str] | str = (),
        **_: Any,
    ) -> list[Figure]:
        spec = ctx.je_type_args.get("reperformance")
        if spec is None:
            return [self.unable(check_id, "this entry type states no record-level identities")]
        if not isinstance(spec, ReperformanceSpec):  # pragma: no cover - the orchestrator sets it
            raise ToolError("the re-performance basis handed to this tool is not a specification")

        wanted = [identities] if isinstance(identities, str) else list(identities)
        if not wanted:
            return [self.unable(check_id, "no identity was named for this check")]

        bound = ctx.table(table or spec.table)
        rows = _records(bound, spec.record_key)
        return [
            self._identity(ctx, bound, rows, spec, identity_id, check_id) for identity_id in wanted
        ]

    def _identity(
        self,
        ctx: ToolContext,
        bound: BoundTable,
        rows: list[int],
        spec: ReperformanceSpec,
        identity_id: str,
        check_id: str,
    ) -> Figure:
        args: dict[str, Any] = {"table": bound.name, "identity": identity_id}
        identity = spec.identity(identity_id)
        if identity is None:
            # Step 12 cannot catch this: it validates argument *names*, and the ids are
            # business knowledge. The check reports it rather than the figure vanishing.
            logger.warning("check %s names unknown identity %r", check_id, identity_id)
            return self.unable(check_id, f"no identity named {identity_id!r}", args)

        compiled = compile_formula(identity.expression)
        needed = [*sorted(compiled.names), identity.against]
        missing = [meaning for meaning in needed if not bound.has(meaning)]
        if missing:
            return self.figure(
                check_id,
                ok=None,
                summary=(
                    f"{identity.label}: this workbook has no "
                    f"{', '.join(missing)} column, so the arithmetic cannot be re-performed"
                ),
                args=args,
                error=f"{identity.id}_columns_missing",
            )

        headroom = identity.tolerance if identity.tolerance is not None else ctx.tolerance
        diffs: list[_RowDiff] = []
        checked = tied = 0
        values: dict[str, Decimal | None] = {}

        for row in rows:
            values.clear()
            for meaning in compiled.names:
                values[meaning] = bound.number(row, meaning)
            expected = compiled.evaluate(values)
            actual = bound.number(row, identity.against)
            if expected is None or actual is None:
                continue
            checked += 1
            difference = expected - actual
            if abs(difference) <= headroom:
                tied += 1
            else:
                diffs.append(
                    _RowDiff(
                        row=row,
                        cell=bound.cell_ref(row, identity.against).cell,
                        expected=expected,
                        actual=actual,
                        difference=difference,
                    )
                )

        coverage = RowCoverage(rows_total=len(rows), rows_bound=checked, rows_tied=tied)
        if (
            identity.min_tie_rate is not None
            and coverage.rows_bound
            and coverage.tie_rate < identity.min_tie_rate
        ):
            return self.figure(
                check_id,
                ok=None,
                summary=(
                    f"{identity.label}: only {coverage.rows_tied:,} of "
                    f"{coverage.rows_bound:,} records agree, so this workbook derives the "
                    "figure some other way and it cannot be re-performed from the columns "
                    "present"
                ),
                coverage=coverage,
                args=args,
                error=f"{identity.id}_convention_unknown",
            )

        impact = sum((abs(diff.difference) for diff in diffs), Decimal(0))
        unbound = coverage.rows_total - coverage.rows_bound
        return self.figure(
            check_id,
            # Complete coverage *and* nothing differing. Either alone is not a pass.
            ok=coverage.complete,
            summary=(
                f"{identity.label}: {coverage.rows_tied:,} of {coverage.rows_total:,} rows agree"
                f"{f', {len(diffs):,} differ by {impact} in total' if diffs else ''}"
                f"{f', {unbound:,} row(s) had no comparable figure' if unbound else ''}"
            ),
            exception_count=len(diffs) + unbound,
            financial_impact=impact or None,
            coverage=coverage,
            samples=worst_first([diff.as_sample() for diff in diffs]),
            numbers=[impact, *(diff.difference for diff in diffs)],
            args=args,
        )


def _records(table: BoundTable, key: str) -> list[int]:
    """Rows that are actually records, not the blanks and totals beneath them.

    A profiled block ends at a run of empty rows, so it reaches past the last record into
    trailing subtotal and spacer rows. Counting those as rows that failed to tie turns a
    clean workbook into a failure, which is the worst kind of wrong answer.
    """
    if not table.has(key):
        return list(table.rows)
    return [row for row in table.rows if table.text(row, key)]


__all__ = ["TOOL_NAME", "ReperformTool", "RowIdentitySpec"]
