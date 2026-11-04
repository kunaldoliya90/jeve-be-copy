"""Evidence appropriateness, as a figure: AI-E's verdict, turned into the one currency a
check's status is allowed to be set in.

Named here because the orchestrator runs this one step apart from the rest of the plan, the
same way it already does the entry recalculation and the trend check: it needs a model call
against evidence content, not workbook arithmetic, and it has to resolve before anything
else is dispatched — see ``review.orchestrator.orchestrator`` for the gate itself.
"""

import logging
from decimal import Decimal
from typing import Any, Final

from review.agents.evidence_appropriateness import EvidenceAppropriatenessVerdict
from review.models.evidence import Figure
from review.tools.base import BaseTool, ToolContext

logger = logging.getLogger(__name__)

TOOL_NAME: Final = "evidence_appropriateness"


class EvidenceAppropriatenessTool(BaseTool):
    """Does the supporting evidence relate to this entry, or to some other one?

    Judged from evidence content rather than from any figure another check produced, so it
    cannot be settled by the generic ``decided_by: figures``/``judgement`` routing — see
    ``review.agents.evidence_appropriateness`` for why this is a standalone model call
    instead.
    """

    name = TOOL_NAME
    signature: dict[str, Any] = {}  # noqa: RUF012

    def run(self, ctx: ToolContext, check_id: str, **_: Any) -> list[Figure]:
        # The orchestrator always splits this check out of the plan and calls
        # `figures_for_evidence_appropriateness` directly, before anything else runs.
        # Reaching this is a wiring defect, not a workbook one.
        return [
            self.unable(check_id, "evidence appropriateness did not run as its own gated step")
        ]


def figures_for_evidence_appropriateness(
    verdict: EvidenceAppropriatenessVerdict | None, check_id: str, **args: Any
) -> list[Figure]:
    """Turn AI-E's verdict into a figure, never raising.

    ``verdict`` is ``None`` both when no model was configured and, for now, every other time:
    the orchestrator does not call AI-E at all (see
    `review.orchestrator.orchestrator._gate_on_evidence`), so this always reports as not
    currently checked — the same honest "nothing was established" every other unjudged check
    in this harness reports, never a quiet pass.
    """
    tool = EvidenceAppropriatenessTool()
    if verdict is None:
        return [
            tool.unable(check_id, "evidence appropriateness is not currently checked", dict(args))
        ]

    samples = [
        {"kind": "mismatch", **mismatch} for mismatch in verdict.mismatched
    ] + [{"kind": "citation", **citation} for citation in verdict.citations]

    return [
        tool.figure(
            check_id,
            ok=verdict.ok,
            summary=verdict.rationale or _default_summary(verdict),
            exception_count=len(verdict.mismatched),
            samples=samples,
            numbers=[Decimal(len(verdict.mismatched)), Decimal(len(verdict.citations))],
            args=dict(args),
        )
    ]


def _default_summary(verdict: EvidenceAppropriatenessVerdict) -> str:
    """A model that returns a status with no rationale text still needs a sentence on it."""
    if verdict.ok is True:
        return "The evidence's identity matches the posting tab."
    if verdict.ok is False:
        return "The evidence does not relate to this entry."
    return "Not enough identity information was available to decide."


__all__ = ["TOOL_NAME", "EvidenceAppropriatenessTool", "figures_for_evidence_appropriateness"]
