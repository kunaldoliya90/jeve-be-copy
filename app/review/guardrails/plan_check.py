"""Step 12. Nothing runs until the plan has been checked against this workbook.

A step is dropped when any of these does not hold, and the reason is kept so the check it
served reports *why* it could not be validated:

1. **The tool exists.** A name the registry does not carry cannot run.
2. **Every argument is one the tool declares.** A tool silently ignoring an argument it does
   not know is how a check comes to measure something other than what was asked.
3. **Every table the step names is bound.** Arguments name meanings from the verified map, so
   a step pointing at a table this workbook does not have is caught here rather than inside
   the tool.
4. **One step per check.** Two figures competing to be decisive is a status nobody chose.

A dropped step is never a silent omission: its check keeps no figures, which the routing
rules read as unproven, and unproven is never a pass.
"""

import logging
from dataclasses import dataclass

from review.models.plan import ExecutionPlan, PlanStep
from review.models.sheet_map import SheetMap
from review.tools import registry

logger = logging.getLogger(__name__)

_REQUIRED_TABLE_ARG = "table"
"""The one argument every tool treats as required.

Deliberately only this one. ``master``, ``hops`` and the ``pii_scan`` lists name tables a
tool degrades gracefully without — it reports the absence in its own figure, which says more
than a dropped step would. Dropping those would turn a partial answer into no answer.
"""


@dataclass(frozen=True, slots=True)
class CheckedPlan:
    """The plan that may run, and an account of everything that may not."""

    plan: ExecutionPlan
    dropped: tuple[tuple[str, str], ...] = ()
    """``(check_id, why)`` per step removed, in the order they were removed."""

    @property
    def dropped_checks(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(check_id for check_id, _ in self.dropped))


def plan_check(plan: ExecutionPlan, sheet_map: SheetMap) -> CheckedPlan:
    """Return the steps that may run, and why the others may not."""
    kept: list[PlanStep] = []
    dropped: list[tuple[str, str]] = []
    claimed: set[str] = set()

    for step in plan.steps:
        problem = _problem(step, sheet_map, claimed)
        if problem is not None:
            dropped.append((step.check_id, problem))
            continue
        claimed.add(step.check_id)
        kept.append(step)

    if dropped:
        logger.warning(
            "plan check dropped %d step(s): %s",
            len(dropped),
            "; ".join(f"{check_id}: {why}" for check_id, why in dropped),
        )

    return CheckedPlan(
        plan=ExecutionPlan(steps=tuple(kept), source=plan.source),
        dropped=tuple(dropped),
    )


def _problem(step: PlanStep, sheet_map: SheetMap, claimed: set[str]) -> str | None:
    """One line saying why this step cannot run, or ``None`` when it can."""
    if step.check_id in claimed:
        return "a step for this check was already planned"

    tool = registry.get(step.tool)
    if tool is None:
        return f"no tool named {step.tool!r}"

    unknown = sorted(set(step.args) - set(tool.signature))
    if unknown:
        return f"{step.tool} does not take {', '.join(unknown)}"

    table = step.args.get(_REQUIRED_TABLE_ARG)
    if isinstance(table, str) and table and table not in sheet_map.tables:
        return f"this workbook has no {table} table"

    return None


__all__ = ["CheckedPlan", "plan_check"]
