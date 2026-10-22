"""AI-M. The methodology tab is prose; the arithmetic needs parameters.

A preparer writes the basis of the entry on a tab in sentences — which denominator the daily
rate uses, which leave types are in scope, whether a negative balance is floored. When that
quietly changes, every other check still passes, because the arithmetic is consistent with
the new basis and nobody decided anything. That is the one error class the figures cannot
catch.

Reading those sentences is what a model is actually good at, and it is safe here for a
specific reason: **the reading never reaches the arithmetic.** It goes to
``methodology_drift``, which diffs it against what the business approved in the JE type file.
Where the two differ, that is the finding. Where the model misreads, the finding is a false
positive a reviewer resolves in a minute — it can never change a computed number.

Conditional and cached on the tab's own text, so the same methodology is read once however
many entries arrive under it.
"""

import hashlib
import json
import logging
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.evidence import MethodologyReading
from review.models.sheet_map import SheetMap, SheetRole
from review.models.workbook_data import Sheet, WorkbookData

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 2_000
MAX_SCAN_ROWS: Final = 200
MAX_SCAN_COLUMNS: Final = 40
MAX_TEXT_CELLS: Final = 300
MIN_TEXT_LENGTH: Final = 3
"""A methodology tab is a page of prose and a small table, never a data sheet."""

METHODOLOGY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "salary_per_day_denominator",
        "eligible_leave_types",
        "balance_floored_at_zero",
        "unread",
    ],
    "properties": {
        "salary_per_day_denominator": {
            "type": ["string", "null"],
            "description": "The basis stated for the daily rate, verbatim. Null if not stated",
        },
        "eligible_leave_types": {"type": "array", "items": {"type": "string"}},
        "balance_floored_at_zero": {
            "type": ["boolean", "null"],
            "description": "Null when the tab does not say, which is not the same as false",
        },
        "unread": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Parameters the tab does not state, named rather than guessed",
        },
    },
}


def methodology_sheet(data: WorkbookData, sheet_map: SheetMap) -> Sheet | None:
    """The methodology tab, found by its role rather than by its name."""
    for name, role in sheet_map.roles.items():
        if role is SheetRole.METHODOLOGY:
            return data.sheet(name)
    return None


def text_of(sheet: Sheet) -> tuple[str, ...]:
    """The tab's written lines, in reading order.

    Text only. A number on a methodology tab is a parameter rather than employee data, but
    reading the prose is the job here, and sending the numeric cells as well would invite
    the model to total something.
    """
    lines = [
        (row, col, cell.value.strip())
        for (row, col), cell in sheet.cells.items()
        if isinstance(cell.value, str)
        and row <= MAX_SCAN_ROWS
        and col <= MAX_SCAN_COLUMNS
        and len(cell.value.strip()) >= MIN_TEXT_LENGTH
    ]
    lines.sort(key=lambda item: (item[0], item[1]))
    return tuple(text for _, _, text in lines[:MAX_TEXT_CELLS])


def digest(lines: tuple[str, ...]) -> str:
    """A key for the reading: the same words read once, however many entries arrive."""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


async def read_methodology(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    lines: tuple[str, ...],
) -> tuple[MethodologyReading | None, LLMResponse | None]:
    """AI-M. ``None`` when there is nothing to read or nothing to read it with.

    ``None`` leaves ``methodology_drift`` comparing only what code can see on the tab — the
    period stamp — and the figure says so. An absent reading never becomes an agreement.
    """
    if provider is None or not lines:
        return None, None

    response = await call_model(
        provider,
        prompts.get(AICall.METHODOLOGY).text,
        {"methodology_tab_lines": list(lines)},
        METHODOLOGY_SCHEMA,
        "methodology",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return None, None

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-M answer was not valid JSON: %s", exc)
        return None, response
    if not isinstance(answer, dict):  # pragma: no cover - the schema forbids it
        return None, response

    denominator = answer.get("salary_per_day_denominator")
    floored = answer.get("balance_floored_at_zero")
    reading = MethodologyReading(
        salary_per_day_denominator=str(denominator).strip() if denominator else None,
        eligible_leave_types=tuple(
            str(item).strip()
            for item in answer.get("eligible_leave_types", [])
            if str(item).strip()
        ),
        balance_floored_at_zero=floored if isinstance(floored, bool) else None,
        unread=tuple(str(item).strip() for item in answer.get("unread", []) if str(item).strip()),
        source_sha256=digest(lines),
    )
    logger.info(
        "AI-M read the methodology: denominator=%r leave_types=%d unread=%d",
        reading.salary_per_day_denominator,
        len(reading.eligible_leave_types),
        len(reading.unread),
    )
    return reading, response


__all__ = [
    "METHODOLOGY_SCHEMA",
    "digest",
    "methodology_sheet",
    "read_methodology",
    "text_of",
]
