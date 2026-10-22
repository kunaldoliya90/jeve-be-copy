"""Every tool the plan may name, and the catalogue AI-2 is shown.

A plan step naming a tool that is not here is dropped by ``plan_check`` rather than failing
the review — which is also why this is the single place a tool becomes callable.
"""

from typing import Final

from review.tools.base import BaseTool
from review.tools.calculation import BalanceTool, ControlTotalsTool, RollupChainTool
from review.tools.entry import (
    AccountMappingTool,
    EvidenceRegisterTool,
    FormulaHealthTool,
    PeriodAgreementTool,
    PIIScanTool,
    ReversalCheckTool,
)
from review.tools.methodology import MethodologyDriftTool
from review.tools.reperformance import (
    ClassificationCheckTool,
    NegativeScanTool,
    PopulationReconcileTool,
    RecalcAccrualTool,
    RecalcLeaveBalanceTool,
)
from review.tools.salary import RecalcSalaryPerDayTool, SalaryValidateTool

_TOOLS: Final[tuple[BaseTool, ...]] = (
    # arithmetic on the posted entry
    BalanceTool(),
    ControlTotalsTool(),
    RollupChainTool(),
    # re-performance, row by row
    RecalcAccrualTool(),
    RecalcLeaveBalanceTool(),
    ClassificationCheckTool(),
    PopulationReconcileTool(),
    NegativeScanTool(),
    RecalcSalaryPerDayTool(),
    SalaryValidateTool(),
    # the entry, its evidence and its hygiene
    AccountMappingTool(),
    ReversalCheckTool(),
    PeriodAgreementTool(),
    EvidenceRegisterTool(),
    FormulaHealthTool(),
    PIIScanTool(),
    MethodologyDriftTool(),
)

TOOLS: Final[dict[str, BaseTool]] = {tool.name: tool for tool in _TOOLS}


def get(name: str) -> BaseTool | None:
    return TOOLS.get(name)


def catalogue() -> list[dict[str, object]]:
    """Name and argument schema per tool. The only thing AI-2 is told about them."""
    return [
        {
            "name": tool.name,
            "description": (tool.__doc__ or "").strip().split("\n")[0],
            "signature": tool.signature,
        }
        for tool in sorted(_TOOLS, key=lambda item: item.name)
    ]
