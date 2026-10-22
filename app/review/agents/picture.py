"""AI-R. The evidence that is a screenshot rather than a number.

Half the support for one of these entries is a picture: the SAP posting screen, the approval
mail, the payroll extract. A review that cannot see those can say the arithmetic holds and
nothing about whether the entry has its evidence — which is the half an auditor is there for.

Two rules make reading them safe.

**Nothing it reads is believed on its own.** The amounts come back as labelled values, and
``evidence_register`` checks them against facts code read from the workbook. A screenshot
whose total does not match the entry is a finding; one the model misread is a finding a
reviewer clears in a minute. Neither can move a computed number.

**``legible: false`` is a real answer.** A screenshot too small or too compressed to read is
missing evidence, and reporting it as such is the honest outcome. Guessing at a blurred total
is the one thing this step must never do.

Cached on the picture's own bytes, so a screenshot that appears in every month's workbook is
read once.
"""

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMImage, LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.evidence import PictureReading
from review.models.workbook_data import PictureData

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 2_000
MAX_AMOUNTS: Final = 12
MIN_PICTURE_BYTES: Final = 4_096
"""Below this a pasted image is an icon, a logo or a border, not a screenshot of anything.
Sending them would cost a call each and return ``legible: false`` every time."""

PICTURE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    # Every key in properties, because strict mode requires exactly that. A field that may
    # have no answer says so with a null type, never by being left out.
    "required": [
        "legible",
        "kind",
        "period",
        "entity",
        "currency",
        "amounts",
        "approval_seen",
    ],
    "properties": {
        "legible": {
            "type": "boolean",
            "description": "False when it cannot be read. A real answer, not a failure",
        },
        "kind": {
            "type": ["string", "null"],
            "description": 'What it shows, e.g. "SAP posting confirmation", "approval email"',
        },
        "period": {"type": ["string", "null"]},
        "entity": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "amounts": {
            "type": "array",
            "maxItems": MAX_AMOUNTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string", "description": "Digits as shown, no grouping"},
                },
            },
        },
        "approval_seen": {"type": "boolean"},
    },
}


def worth_reading(picture: PictureData) -> bool:
    """Whether a pasted image is big enough to be a screenshot of something."""
    return picture.size_bytes >= MIN_PICTURE_BYTES


async def read_picture(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    picture: PictureData,
) -> tuple[PictureReading | None, LLMResponse | None]:
    """AI-R on one screenshot. ``None`` when there is nothing to read it with.

    ``None`` is not the same as an illegible picture: the first says nobody looked, the
    second says somebody looked and could not read it. The evidence register reports them
    differently, and a reviewer needs that difference.
    """
    if provider is None:
        return None, None

    response = await call_model(
        provider,
        prompts.get(AICall.PICTURE_READER).text,
        {
            "where_this_picture_sits": f"{picture.sheet}!{picture.anchor_cell}",
            "read_the_image_supplied_with_this_message": True,
        },
        PICTURE_SCHEMA,
        "picture",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        image=LLMImage(data=picture.data, content_type=picture.content_type),
    )
    if response is None:
        return None, None

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-R answer was not valid JSON: %s", exc)
        return None, response
    if not isinstance(answer, dict):  # pragma: no cover - the schema forbids it
        return None, response

    reading = PictureReading(
        picture_id=picture.picture_id,
        legible=bool(answer.get("legible")),
        kind=_text(answer.get("kind")),
        period=_text(answer.get("period")),
        entity=_text(answer.get("entity")),
        currency=_text(answer.get("currency")),
        amounts=_amounts(answer.get("amounts")),
        approval_seen=bool(answer.get("approval_seen")),
    )
    logger.info(
        "AI-R read %s on %s: legible=%s kind=%r amounts=%d",
        picture.picture_id,
        picture.sheet,
        reading.legible,
        reading.kind,
        len(reading.amounts),
    )
    return reading, response


def _amounts(raw: object) -> tuple[tuple[str, Decimal], ...]:
    """Label and value pairs, dropping anything that is not a number.

    A value the model wrote as prose is discarded rather than coerced: the register compares
    these against the entry, and a zero standing in for "about four thousand" would compare
    cleanly against nothing.
    """
    if not isinstance(raw, list):
        return ()

    pairs: list[tuple[str, Decimal]] = []
    for item in raw[:MAX_AMOUNTS]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        try:
            value = Decimal(str(item.get("value", "")).replace(",", "").replace(" ", ""))
        except (InvalidOperation, ValueError):
            logger.info("dropping an amount AI-R could not express as a number: %r", item)
            continue
        if label:
            pairs.append((label, value))
    return tuple(pairs)


def _text(raw: object) -> str | None:
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


__all__ = ["MIN_PICTURE_BYTES", "PICTURE_SCHEMA", "read_picture", "worth_reading"]
