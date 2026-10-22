"""AI-M: reading the methodology tab into parameters, and what silence means.

Silence is the case that matters. A tab that does not state a denominator must come back
saying so, because the alternative — supplying the usual answer — silently rescales the
whole entry and every other check still agrees with it.
"""

import pytest

from review.agents.methodology import (
    METHODOLOGY_SCHEMA,
    digest,
    methodology_sheet,
    read_methodology,
    text_of,
)
from review.context.prompts import PromptRegistry
from review.models.sheet_map import SheetMap, SheetRole
from review.models.workbook_data import Cell, Sheet, WorkbookData
from tests.doubles import StubLLMProvider

pytestmark = pytest.mark.anyio

LINES = (
    "Vacation accrual methodology",
    "Daily rate uses 21.67 working days per month",
    "Eligible leave: CP and RTT",
)


def _sheet(name: str = "Methodology") -> Sheet:
    return Sheet(
        name=name,
        max_row=3,
        max_column=1,
        cells={(index, 1): Cell(value=text) for index, text in enumerate(LINES, start=1)},
    )


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.load()
    return registry


def _reader(answer: dict[str, object]) -> StubLLMProvider:
    return StubLLMProvider({"methodology": answer})


# --- finding and reading the tab ----------------------------------------------------------


def test_the_tab_is_found_by_its_role_not_its_name() -> None:
    """A preparer can call it anything; the role is what the sheet map established."""
    data = WorkbookData(sheets=(_sheet("Notes"),))
    sheet_map = SheetMap(roles={"Notes": SheetRole.METHODOLOGY})

    found = methodology_sheet(data, sheet_map)

    assert found is not None
    assert found.name == "Notes"


def test_a_workbook_with_no_methodology_tab_has_nothing_to_read() -> None:
    data = WorkbookData(sheets=(_sheet(),))
    sheet_map = SheetMap(roles={"Methodology": SheetRole.IGNORE})

    assert methodology_sheet(data, sheet_map) is None


def test_the_lines_come_back_in_reading_order() -> None:
    assert text_of(_sheet()) == LINES


def test_single_characters_are_not_lines() -> None:
    """Column markers and tick marks are not methodology."""
    sheet = Sheet(name="Methodology", cells={(1, 1): Cell(value="x"), (2, 1): Cell(value="ok")})

    assert text_of(sheet) == ()


def test_the_digest_is_over_the_words_so_the_same_methodology_keys_the_same() -> None:
    assert digest(LINES) == digest(LINES)
    assert digest(LINES) != digest((*LINES, "and one more rule"))


# --- the reading itself -------------------------------------------------------------------


async def test_no_provider_means_no_reading(prompts: PromptRegistry) -> None:
    reading, response = await read_methodology(None, prompts, LINES)

    assert reading is None
    assert response is None


async def test_no_lines_means_no_call(prompts: PromptRegistry) -> None:
    stub = _reader({})

    reading, _ = await read_methodology(stub, prompts, ())

    assert reading is None
    assert stub.requests == []


async def test_a_stated_basis_comes_back_verbatim(prompts: PromptRegistry) -> None:
    stub = _reader(
        {
            "salary_per_day_denominator": "21.67 working days per month",
            "eligible_leave_types": ["CP", "RTT"],
            "balance_floored_at_zero": True,
            "unread": [],
        }
    )

    reading, response = await read_methodology(stub, prompts, LINES)

    assert reading is not None
    assert reading.salary_per_day_denominator == "21.67 working days per month"
    assert reading.eligible_leave_types == ("CP", "RTT")
    assert reading.balance_floored_at_zero is True
    assert reading.source_sha256 == digest(LINES)
    assert response is not None


async def test_a_parameter_the_tab_does_not_state_comes_back_as_unstated(
    prompts: PromptRegistry,
) -> None:
    """Null, not a default. An assumed denominator rescales the whole entry."""
    stub = _reader(
        {
            "salary_per_day_denominator": None,
            "eligible_leave_types": [],
            "balance_floored_at_zero": None,
            "unread": ["salary per day denominator"],
        }
    )

    reading, _ = await read_methodology(stub, prompts, LINES)

    assert reading is not None
    assert reading.salary_per_day_denominator is None
    assert reading.balance_floored_at_zero is None
    assert reading.unread == ("salary per day denominator",)


async def test_a_non_boolean_flooring_answer_reads_as_not_stated(
    prompts: PromptRegistry,
) -> None:
    stub = _reader(
        {
            "salary_per_day_denominator": "x",
            "eligible_leave_types": [],
            "balance_floored_at_zero": "probably",
            "unread": [],
        }
    )

    reading, _ = await read_methodology(stub, prompts, LINES)

    assert reading is not None
    assert reading.balance_floored_at_zero is None


async def test_the_model_sees_the_lines_and_nothing_else(prompts: PromptRegistry) -> None:
    stub = _reader({})

    await read_methodology(stub, prompts, LINES)

    payload = stub.payload("methodology")
    assert payload == {"methodology_tab_lines": list(LINES)}


def test_the_schema_allows_a_null_for_every_parameter_that_can_be_unstated() -> None:
    properties = METHODOLOGY_SCHEMA["properties"]

    assert "null" in properties["salary_per_day_denominator"]["type"]
    assert "null" in properties["balance_floored_at_zero"]["type"]
    assert "unread" in METHODOLOGY_SCHEMA["required"]
