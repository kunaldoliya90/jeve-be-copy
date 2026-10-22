"""Step 13. Run the validated plan, concurrently, and number the figures.

It runs a plan rather than a JE type on purpose: by the time anything reaches here, step 12
has checked every step against this workbook, so a tool is never called with an argument it
does not take or a table the map never bound.

Tools are pure and CPU-bound, so they fan out across a thread pool. Order is restored
afterwards: figure ids are assigned in **plan order**, not completion order, so ``F-07``
means the same thing to a reviewer reading the report as to an engineer reading the trace.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from review.models.evidence import Figure
from review.models.plan import ExecutionPlan, PlanStep
from review.tools import registry
from review.tools.base import BaseTool, ToolContext, assign_figure_ids, run_tool

logger = logging.getLogger(__name__)


def run_checks(
    plan: ExecutionPlan,
    ctx: ToolContext,
    *,
    max_workers: int,
    on_finished: Callable[[str, int], None] | None = None,
) -> dict[str, tuple[Figure, ...]]:
    """The evidence pack: check id -> its figures, in plan order.

    ``max_workers`` is ``config.TOOL_WORKERS``, passed down by the orchestrator: the pool
    size is a deployment's business, not this module's.

    A step naming a tool that does not exist produces an unable-to-validate figure rather
    than vanishing. A check that vanished would leave an area scored on nothing at all.
    """
    results: dict[str, list[Figure]] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool") as pool:
        futures = {pool.submit(_run_one, step, ctx): step for step in plan.steps}
        for future in futures:
            step = futures[future]
            figures = future.result()
            results[step.check_id] = figures
            if on_finished is not None:
                on_finished(step.check_id, len(figures))

    pack = {step.check_id: assign_figure_ids(results.get(step.check_id, [])) for step in plan.steps}
    logger.info(
        "ran %d check(s), %d figure(s)",
        len(pack),
        sum(len(figures) for figures in pack.values()),
    )
    return pack


def _run_one(step: PlanStep, ctx: ToolContext) -> list[Figure]:
    tool = registry.get(step.tool)
    if tool is None:
        # Step 12 drops these, so reaching here means the registry changed under a cached
        # plan. The check reports it rather than disappearing.
        logger.warning("check %s names unknown tool %r", step.check_id, step.tool)
        return [
            _MissingTool(step.tool).unable(
                step.check_id, f"no tool named {step.tool!r}", dict(step.args)
            )
        ]

    return run_tool(tool, ctx, step.check_id, **step.args)


class _MissingTool(BaseTool):
    """Stands in for a tool a context file names but the build does not have."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, ctx: ToolContext, check_id: str, **args: object) -> list[Figure]:
        return [self.unable(check_id, f"no tool named {self.name!r}")]
