"""AI-1 Understand. A second, independent reading of what this workbook is.

Tier 3 of identification. Tiers 1 and 2 — the layout cache and the shape discriminators —
have already answered by the time this runs, and **this call never overrides them.** The
tools bind to code's columns either way, because a model that could move a binding could
move the arithmetic, and the one guarantee the harness makes is that it cannot.

What it is for is corroboration, on the same principle as the challenge agent: a second
reader shown the evidence and not the first reader's answer. Agreement raises the confidence
recorded against the type. Disagreement sends the review to a person. Neither outcome
changes a number.

It is given the structure context and a deliberately symmetric catalogue — id, name, aliases
and a one-line objective per type, nothing richer for one than another. Describe one type
better than the rest and the classifier measures description length instead of evidence.
"""

import json
import logging
from decimal import Decimal
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.sheet_map import SheetRole
from review.models.workbook import StructureContext

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 4_000
MAX_CITATIONS: Final = 8
MAX_SAMPLE_VALUES: Final = 4
MAX_HEADERS: Final = 40
"""Headers per sheet. A wide calculation tab has forty-two of them and the first forty carry
the shape; the cap is what keeps one sheet from crowding out the other nine."""

UNDERSTANDING_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["je_type", "confidence", "citations", "roles"],
    "properties": {
        "je_type": {
            "type": "string",
            "description": 'An id from the catalogue, or "unknown" if nothing fits',
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "citations": {
            "type": "array",
            "maxItems": MAX_CITATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sheet", "cell", "why"],
                "properties": {
                    "sheet": {"type": "string"},
                    "cell": {"type": "string", "description": 'An A1 reference, e.g. "AL35"'},
                    "why": {"type": "string"},
                },
            },
        },
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sheet", "role"],
                "properties": {
                    "sheet": {"type": "string"},
                    "role": {"type": "string", "enum": [role.value for role in SheetRole]},
                },
            },
        },
    },
}


class Understanding:
    """What AI-1 said. Believed only as far as ``map_check`` can verify it."""

    __slots__ = ("citations", "confidence", "je_type", "roles")

    def __init__(
        self,
        je_type: str = "unknown",
        confidence: float = 0.0,
        citations: tuple[dict[str, str], ...] = (),
        roles: dict[str, str] | None = None,
    ) -> None:
        self.je_type = je_type
        self.confidence = confidence
        self.citations = citations
        self.roles = roles or {}

    def __repr__(self) -> str:
        return f"Understanding({self.je_type}, {self.confidence:.2f}, {len(self.citations)} cites)"


async def understand(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    structure: StructureContext,
    catalogue: tuple[dict[str, Any], ...],
) -> tuple[Understanding | None, LLMResponse | None]:
    """AI-1. ``None`` when no model ran, which is a state and not a failure.

    A review with no second reading records that the type was not independently corroborated
    rather than quietly reporting the shape's answer as agreed.
    """
    if provider is None:
        return None, None

    response = await call_model(
        provider,
        prompts.get(AICall.UNDERSTAND).text,
        understand_input(structure, catalogue),
        UNDERSTANDING_SCHEMA,
        "understanding",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return None, None

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-1 answer was not valid JSON: %s", exc)
        return None, response
    if not isinstance(answer, dict):  # pragma: no cover - the schema forbids it
        return None, response

    understanding = Understanding(
        je_type=str(answer.get("je_type", "unknown")),
        confidence=_fraction(answer.get("confidence")),
        citations=tuple(
            {
                "sheet": str(item.get("sheet", "")),
                "cell": str(item.get("cell", "")),
                "why": str(item.get("why", "")),
            }
            for item in answer.get("citations", [])
            if isinstance(item, dict)
        ),
        roles={
            str(item.get("sheet", "")): str(item.get("role", ""))
            for item in answer.get("roles", [])
            if isinstance(item, dict)
        },
    )
    logger.info(
        "AI-1 read this as %s at %.2f with %d citation(s)",
        understanding.je_type,
        understanding.confidence,
        len(understanding.citations),
    )
    return understanding, response


def understand_input(
    structure: StructureContext, catalogue: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    """What AI-1 sees: the workbook's shape, and every type it could be.

    The catalogue and the role vocabulary come first and are identical across reviews, so
    the provider's prefix cache hits; the per-workbook half goes last. Absent from all of
    it: the type the preparer selected, and the answer the shape rules already gave.
    """
    return {
        "journal_entry_types_this_system_knows": list(catalogue),
        "sheet_roles_you_may_assign": [role.value for role in SheetRole],
        "workbook": {
            "sheets": [_sheet(sheet) for sheet in structure.sheets],
            "feeds_from": list(structure.lineage),
            "defects": list(structure.defects_summary),
            "shape_was_trimmed_to_fit": structure.trimmed,
        },
    }


def _sheet(sheet: Any) -> dict[str, Any]:
    """One sheet's shape. Column totals and category codes cross; employee values do not."""
    return {
        "name": sheet.name,
        "hidden": sheet.hidden,
        "row_count": sheet.row_count,
        "headers": list(sheet.headers[:MAX_HEADERS]),
        "column_types": [column.value for column in sheet.column_types[:MAX_HEADERS]],
        "column_totals": {
            letter: str(total)
            for letter, total in sheet.totals.items()
            if isinstance(total, Decimal)
        },
        "category_samples": {
            letter: list(values[:MAX_SAMPLE_VALUES]) for letter, values in sheet.samples.items()
        },
    }


def _fraction(raw: object) -> float:
    """A confidence outside 0..1 is a malformed answer, not a strong opinion."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, value))


__all__ = ["UNDERSTANDING_SCHEMA", "Understanding", "understand", "understand_input"]
