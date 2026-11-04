"""AI-E Evidence appropriateness. Does the supporting evidence relate to THIS entry?

A different question from evidence completeness, and asked a different way. Completeness
counts which required layers are present; this reads what is actually in them and compares
it against the one thing in the workbook nobody disputes is the entry itself — the posting
tab, the preparer's own record of what was booked. Everything else — the calculation sheet,
the employee master, the methodology tab, a pasted screenshot — is evidence, and evidence for
the wrong entity is not incomplete evidence. It is the wrong evidence.

Run as its own step, before anything else, for the same reason `understand`/`map_check` are:
a conclusion about whether the evidence is even about this entry has to exist before any
other check's numbers are worth looking at. See `review.tools.evidence_appropriateness` for
how this verdict becomes a check outcome and `review.orchestrator.orchestrator` for the gate.
"""

import json
import logging
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.context import JETypeContext
from review.models.verdicts import Status
from review.tools.base import BoundTable, ToolContext

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 3_000
MAX_CITATIONS: Final = 8
MAX_IDENTITY_VALUES: Final = 10
"""Distinct values per identifying column, per table. A sample, not a dump: the model is
comparing entities, not auditing every row."""

AUTHORITY_TABLE: Final = "je_posting"
"""What the entry itself is read off. The preparer's own claim, same principle `map_check`
already applies when it treats the posting tab's labels as assertion rather than evidence."""

_IDENTITY_MEANINGS: Final = (
    "company_code",
    "company_name",
    "cost_center",
    "employee_ref",
    "account",
)
"""Meanings worth comparing across tables. Not every table binds all of them, and a table
binding none of them contributes nothing to the comparison — which is itself informative to
the model, not an error."""

_STATUS_BY_ANSWER: Final[dict[str, Status]] = {
    "pass": Status.PASS,
    "fail": Status.FAIL,
    "inconclusive": Status.UNABLE_TO_VALIDATE,
}

EVIDENCE_APPROPRIATENESS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "rationale", "mismatched_attributes", "citations"],
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "inconclusive"]},
        "rationale": {
            "type": "string",
            "description": "What was compared and why it settled this way. Shown to a reviewer.",
        },
        "mismatched_attributes": {
            "type": "array",
            "maxItems": MAX_CITATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["attribute", "posting_tab_says", "evidence_says"],
                "properties": {
                    "attribute": {"type": "string"},
                    "posting_tab_says": {"type": "string"},
                    "evidence_says": {"type": "string"},
                },
            },
        },
        "citations": {
            "type": "array",
            "maxItems": MAX_CITATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "value", "why"],
                "properties": {
                    "table": {"type": "string"},
                    "value": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
    },
}


class EvidenceAppropriatenessVerdict:
    """What AI-E said. ``ok`` is the three-way answer collapsed to what a figure needs."""

    __slots__ = ("citations", "mismatched", "rationale", "status")

    def __init__(
        self,
        status: Status = Status.UNABLE_TO_VALIDATE,
        rationale: str = "",
        mismatched: tuple[dict[str, str], ...] = (),
        citations: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.status = status
        self.rationale = rationale
        self.mismatched = mismatched
        self.citations = citations

    @property
    def ok(self) -> bool | None:
        """``None`` covers both "inconclusive" and anything this module could not parse —
        both are "not established", and neither is a fail."""
        return {Status.PASS: True, Status.FAIL: False}.get(self.status)

    def __repr__(self) -> str:
        return f"EvidenceAppropriatenessVerdict({self.status.value})"


async def assess_evidence_appropriateness(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    je_spec: JETypeContext,
    ctx: ToolContext,
) -> tuple[EvidenceAppropriatenessVerdict | None, LLMResponse | None]:
    """AI-E. ``None`` when no model ran, which is a state and not a failure.

    A review with no model configured records that evidence appropriateness was never
    established, the same way an unjudged check does everywhere else in this harness —
    it does not default to a pass just because nobody could check.
    """
    if provider is None:
        return None, None

    response = await call_model(
        provider,
        prompts.get(AICall.EVIDENCE_APPROPRIATENESS).text,
        evidence_appropriateness_input(je_spec, ctx),
        EVIDENCE_APPROPRIATENESS_SCHEMA,
        "evidence_appropriateness",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return None, response

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-E answer was not valid JSON: %s", exc)
        return None, response
    if not isinstance(answer, dict):  # pragma: no cover - the schema forbids it
        return None, response

    verdict = EvidenceAppropriatenessVerdict(
        status=_STATUS_BY_ANSWER.get(str(answer.get("status", "")), Status.UNABLE_TO_VALIDATE),
        rationale=str(answer.get("rationale", "")),
        mismatched=tuple(
            {
                "attribute": str(item.get("attribute", "")),
                "posting_tab_says": str(item.get("posting_tab_says", "")),
                "evidence_says": str(item.get("evidence_says", "")),
            }
            for item in answer.get("mismatched_attributes", [])
            if isinstance(item, dict)
        ),
        citations=tuple(
            {
                "table": str(item.get("table", "")),
                "value": str(item.get("value", "")),
                "why": str(item.get("why", "")),
            }
            for item in answer.get("citations", [])
            if isinstance(item, dict)
        ),
    )
    logger.info(
        "AI-E read this as %s (%d mismatch(es), %d citation(s))",
        verdict.status.value,
        len(verdict.mismatched),
        len(verdict.citations),
    )
    return verdict, response


def evidence_appropriateness_input(je_spec: JETypeContext, ctx: ToolContext) -> dict[str, Any]:
    """What AI-E sees: the posting tab's own identity, everything else beside it.

    The posting tab is never in `evidence_tables` — it is the thing being compared against,
    not a piece of evidence itself. A table the sheet map never bound to anything simply has
    no identity columns to report, which is itself an honest answer rather than an omission.
    """
    posting = ctx.optional_table(AUTHORITY_TABLE)
    return {
        "journal_entry_type": {
            "id": je_spec.id,
            "name": je_spec.name,
            "objective": je_spec.objective,
        },
        "posting_tab": (
            {"table": AUTHORITY_TABLE, "identity_columns": _identity_values(posting)}
            if posting is not None
            else None
        ),
        "evidence_tables": {
            name: {"identity_columns": _identity_values(table)}
            for name in ctx.sheet_map.tables
            if name != AUTHORITY_TABLE and (table := ctx.optional_table(name)) is not None
        },
        "pictures": [
            {
                "legible": reading.legible,
                "kind": reading.kind,
                "entity": reading.entity,
                "period": reading.period,
                "currency": reading.currency,
            }
            for reading in ctx.picture_readings.values()
        ],
    }


def _identity_values(table: BoundTable) -> dict[str, list[str]]:
    """Distinct values of each identifying meaning the table actually binds, capped."""
    values: dict[str, list[str]] = {}
    for meaning in _IDENTITY_MEANINGS:
        if not table.has(meaning):
            continue
        seen: dict[str, None] = {}
        for row in table.rows:
            text = table.text(row, meaning)
            if text:
                seen[text] = None
            if len(seen) >= MAX_IDENTITY_VALUES:
                break
        if seen:
            values[meaning] = list(seen)
    return values


__all__ = [
    "AUTHORITY_TABLE",
    "EVIDENCE_APPROPRIATENESS_SCHEMA",
    "EvidenceAppropriatenessVerdict",
    "assess_evidence_appropriateness",
    "evidence_appropriateness_input",
]
