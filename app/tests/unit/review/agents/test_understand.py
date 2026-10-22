"""AI-1: what it is shown, and how little of its answer is taken on trust."""

from decimal import Decimal
from typing import Any

import pytest

from review.agents.understand import UNDERSTANDING_SCHEMA, understand, understand_input
from review.context.prompts import PromptRegistry
from review.models.sheet_map import SheetRole
from review.models.workbook import ColumnType, SheetSummary, StructureContext
from tests.doubles import StubLLMProvider

pytestmark = pytest.mark.anyio

FINGERPRINT = "a" * 64

CATALOGUE: tuple[dict[str, Any], ...] = (
    {
        "id": "vacation-accrual-france",
        "name": "Vacation accrual (France)",
        "aliases": ["conges payes"],
        "objective": "Accrue unused leave",
    },
)


def _structure() -> StructureContext:
    return StructureContext(
        layout_fingerprint=FINGERPRINT,
        sheets=(
            SheetSummary(
                name="GPAY",
                row_count=2_273,
                headers=("Matricule", "Total Provision CP"),
                column_types=(ColumnType.TEXT, ColumnType.NUMERIC),
                totals={"B": Decimal("27905812.64")},
                samples={"A": ("FR01", "FR02")},
            ),
        ),
        lineage=("JE Upload!F <- GPAY!AL",),
        defects_summary=("3 #REF! cells on Working",),
    )


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.load()
    return registry


# --- what crosses the boundary ------------------------------------------------------------


def test_the_input_carries_shape_and_labels_but_no_cell_values() -> None:
    payload = understand_input(_structure(), CATALOGUE)

    sheet = payload["workbook"]["sheets"][0]
    assert sheet["headers"] == ["Matricule", "Total Provision CP"]
    assert sheet["column_totals"] == {"B": "27905812.64"}
    assert sheet["category_samples"] == {"A": ["FR01", "FR02"]}
    assert "cells" not in sheet


def test_the_input_never_says_which_type_was_selected_or_what_code_concluded() -> None:
    """Blind by construction. A hint here turns a second reading into an echo."""
    payload = understand_input(_structure(), CATALOGUE)

    assert set(payload) == {
        "journal_entry_types_this_system_knows",
        "sheet_roles_you_may_assign",
        "workbook",
    }
    assert payload["journal_entry_types_this_system_knows"] == list(CATALOGUE)


def test_the_catalogue_comes_before_the_workbook_so_the_prefix_cache_hits() -> None:
    """The shared half is identical across reviews; the per-file half goes last."""
    keys = list(understand_input(_structure(), CATALOGUE))

    assert keys.index("journal_entry_types_this_system_knows") < keys.index("workbook")


def test_every_role_the_schema_allows_is_a_role_the_system_understands() -> None:
    roles = UNDERSTANDING_SCHEMA["properties"]["roles"]["items"]["properties"]["role"]["enum"]

    assert set(roles) == {role.value for role in SheetRole}


def test_the_schema_forbids_fields_nobody_asked_for() -> None:
    assert UNDERSTANDING_SCHEMA["additionalProperties"] is False


# --- what comes back ----------------------------------------------------------------------


async def test_no_provider_means_no_reading_rather_than_an_empty_one(
    prompts: PromptRegistry,
) -> None:
    """``None`` is the signal that nothing corroborated the type; a blank answer is not."""
    answer, response = await understand(None, prompts, _structure(), CATALOGUE)

    assert answer is None
    assert response is None


async def test_a_well_formed_answer_is_read_back_whole(prompts: PromptRegistry) -> None:
    stub = StubLLMProvider(
        {
            "understanding": {
                "je_type": "vacation-accrual-france",
                "confidence": 0.85,
                "citations": [{"sheet": "GPAY", "cell": "AL35", "why": "CP provision"}],
                "roles": [{"sheet": "GPAY", "role": "calculation"}],
            }
        }
    )

    answer, response = await understand(stub, prompts, _structure(), CATALOGUE)

    assert answer is not None
    assert answer.je_type == "vacation-accrual-france"
    assert answer.confidence == 0.85
    assert answer.citations == ({"sheet": "GPAY", "cell": "AL35", "why": "CP provision"},)
    assert answer.roles == {"GPAY": "calculation"}
    assert response is not None


async def test_a_confidence_outside_zero_to_one_is_a_malformed_answer_not_a_strong_one(
    prompts: PromptRegistry,
) -> None:
    stub = StubLLMProvider(
        {"understanding": {"je_type": "x", "confidence": 42, "citations": [], "roles": []}}
    )

    answer, _ = await understand(stub, prompts, _structure(), CATALOGUE)

    assert answer is not None
    assert answer.confidence == 1.0


async def test_an_answer_with_no_type_reads_as_unknown(prompts: PromptRegistry) -> None:
    stub = StubLLMProvider({"understanding": {}})

    answer, _ = await understand(stub, prompts, _structure(), CATALOGUE)

    assert answer is not None
    assert answer.je_type == "unknown"
    assert answer.confidence == 0.0


async def test_a_malformed_citation_is_dropped_without_losing_the_answer(
    prompts: PromptRegistry,
) -> None:
    stub = StubLLMProvider(
        {
            "understanding": {
                "je_type": "vacation-accrual-france",
                "confidence": 0.9,
                "citations": [{"sheet": "GPAY", "cell": "AL35", "why": "ok"}, "not an object"],
                "roles": [],
            }
        }
    )

    answer, _ = await understand(stub, prompts, _structure(), CATALOGUE)

    assert answer is not None
    assert len(answer.citations) == 1
