"""Trend, as a tool: whether the posted amount moves in line with recent history.

Named here because the orchestrator runs this one step apart from the rest of the plan,
the same way it does the entry recalculation: it needs this review's own total, which only
exists once step 13c has produced it, and a baseline the review store holds rather than the
workbook, which no other tool touches. Tools stay pure — the lookup is the orchestrator's
job, at step 13d, and ``run_trend_check`` below takes the baseline as a plain argument.
"""

import logging
from decimal import Decimal
from typing import Any, Final, NamedTuple

from review.models.evidence import Figure
from review.tools.base import BaseTool, ToolContext

logger = logging.getLogger(__name__)

TOOL_NAME: Final = "trend_check"


class TrendBaseline(NamedTuple):
    """What the orchestrator looked up before this check could run."""

    amounts: tuple[Decimal, ...]
    """Prior periods' booked (or, failing that, recomputed) totals. Empty when none fell
    inside the lookback window — a new entry type has no trend yet, and that is not a
    finding."""
    lookback_months: int
    max_change_pct: float | None
    """``None`` until the business sets it. The check can only report the deviation then,
    never fail on it — see ``business_rules.yaml``."""


class TrendCheckTool(BaseTool):
    """Does the posted amount move in line with the entry type's recent history?

    Compared against the average of the prior periods, not against any single one: a
    single unusual month is not a trend, and averaging is what keeps one noisy prior period
    from failing every review that follows it.
    """

    name = TOOL_NAME
    signature: dict[str, Any] = {}  # noqa: RUF012

    def run(self, ctx: ToolContext, check_id: str, **_: Any) -> list[Figure]:
        # The orchestrator always splits this check out of the plan and calls
        # ``run_trend_check`` directly, the same way it does ``recalc_entry_total``.
        # Reaching this is a wiring defect, not a workbook one.
        return [self.unable(check_id, "the trend check did not run as its own step")]


def run_trend_check(
    baseline: TrendBaseline, current_total: Decimal | None, check_id: str, **args: Any
) -> list[Figure]:
    """Compare ``current_total`` against ``baseline``, never raising.

    Three outcomes, not two. No prior data is a pass: there is nothing to measure against,
    which is not the same claim as the entry having passed a comparison. A deviation under
    the threshold is a pass; one at or past it is a fail. ``max_change_pct`` unset is read
    the same way: a pass, since nothing here can prove a deviation excessive without one,
    but the deviation itself is still reported so a reviewer sees what a threshold would
    apply to once the business sets one.
    """
    tool = TrendCheckTool()
    if current_total is None:
        return [
            tool.unable(
                check_id, "the entry's posted amount could not be established", dict(args)
            )
        ]
    if not baseline.amounts:
        return [
            tool.figure(
                check_id,
                ok=True,
                summary=(
                    f"No prior review of this entry type fell inside the "
                    f"{baseline.lookback_months}-month lookback, so there is nothing yet to "
                    "compare the posted amount against"
                ),
                numbers=[current_total],
                args=dict(args),
            )
        ]

    average = sum(baseline.amounts, Decimal(0)) / len(baseline.amounts)
    change_pct = float(abs(current_total - average) / abs(average) * 100) if average else None

    if baseline.max_change_pct is None:
        return [
            tool.figure(
                check_id,
                ok=True,
                summary=(
                    f"The posted amount is {_pct(change_pct)} from the "
                    f"{len(baseline.amounts)}-period average of {average:,.2f}; no deviation "
                    "threshold is configured yet, so nothing here can fail"
                ),
                numbers=[current_total, average],
                args=dict(args),
            )
        ]

    within = change_pct is not None and change_pct <= baseline.max_change_pct
    return [
        tool.figure(
            check_id,
            ok=within,
            summary=(
                f"The posted amount is {_pct(change_pct)} from the "
                f"{len(baseline.amounts)}-period average of {average:,.2f}, against a "
                f"{baseline.max_change_pct:g}% limit"
            ),
            exception_count=0 if within else 1,
            numbers=[current_total, average],
            args=dict(args),
        )
    ]


def _pct(value: float | None) -> str:
    return "an unmeasurable distance" if value is None else f"{value:.1f}%"
