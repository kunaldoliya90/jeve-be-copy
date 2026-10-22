"""Strict JSON schemas for the two judging calls.

The provider enforces these, so a malformed answer fails at the API rather than in a parser
that has to guess. Every constraint below is one the guardrails would otherwise have to
catch after the fact: statuses are an enum, evidence ids match the figure id pattern, and a
conclusion has a length a register cell can hold.

``additionalProperties: false`` and a complete ``required`` list are not style choices —
strict mode rejects a schema without them.
"""

from typing import Any, Final

from review.models.verdicts import Status

_STATUSES: Final = [status.value for status in Status]
_FIGURE_ID: Final = r"^F-\d{2,3}$"

MAX_CONCLUSION_CHARS: Final = 400
MAX_FINDINGS: Final = 8


VERDICTS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id", "status", "conclusion", "findings"],
                "properties": {
                    "check_id": {"type": "string"},
                    "status": {"type": "string", "enum": _STATUSES},
                    "conclusion": {"type": "string", "maxLength": MAX_CONCLUSION_CHARS},
                    "findings": {
                        "type": "array",
                        "maxItems": MAX_FINDINGS,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "severity", "text", "evidence_ids"],
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["exception", "observation"],
                                },
                                "severity": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                                "text": {"type": "string"},
                                # Figure ids only. Anything else is an unverified citation
                                # and sends the check to a person.
                                "evidence_ids": {
                                    "type": "array",
                                    "items": {"type": "string", "pattern": _FIGURE_ID},
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


CHALLENGES_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["challenges"],
    "properties": {
        "challenges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id", "agrees", "status_should_be", "reason", "evidence_ids"],
                "properties": {
                    "check_id": {"type": "string"},
                    "agrees": {"type": "boolean"},
                    # Nullable rather than optional: strict mode requires every property,
                    # and "no change proposed" has to be expressible.
                    "status_should_be": {
                        "anyOf": [
                            {"type": "string", "enum": _STATUSES},
                            {"type": "null"},
                        ]
                    },
                    "reason": {"type": "string", "maxLength": MAX_CONCLUSION_CHARS},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string", "pattern": _FIGURE_ID},
                    },
                },
            },
        }
    },
}
