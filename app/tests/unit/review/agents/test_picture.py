"""AI-R: reading a pasted screenshot, and the limits on believing what it says."""

from decimal import Decimal

import pytest

from review.agents.picture import (
    MIN_PICTURE_BYTES,
    PICTURE_SCHEMA,
    read_picture,
    worth_reading,
)
from review.context.prompts import PromptRegistry
from review.models.workbook_data import PictureData
from tests.doubles import StubLLMProvider

pytestmark = pytest.mark.anyio


def _picture(size: int = 50_000) -> PictureData:
    return PictureData(
        picture_id="P1",
        sheet="Batch JE",
        anchor_cell="AD9",
        sha256="a" * 64,
        content_type="image/png",
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * size,
    )


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.load()
    return registry


def _reader(answer: dict[str, object]) -> StubLLMProvider:
    return StubLLMProvider({"picture": answer})


# --- what is worth a call -----------------------------------------------------------------


def test_a_screenshot_is_worth_reading() -> None:
    assert worth_reading(_picture())


def test_an_icon_is_not() -> None:
    """A logo or a border would cost a call each and come back illegible every time."""
    assert not worth_reading(_picture(size=10))


def test_the_size_floor_is_inclusive() -> None:
    picture = _picture(size=MIN_PICTURE_BYTES)

    assert worth_reading(picture)


# --- reading ------------------------------------------------------------------------------


async def test_no_provider_means_nobody_looked(prompts: PromptRegistry) -> None:
    """Different from an illegible picture, and the register reports them differently."""
    reading, response = await read_picture(None, prompts, _picture())

    assert reading is None
    assert response is None


async def test_a_legible_screenshot_comes_back_with_its_amounts(
    prompts: PromptRegistry,
) -> None:
    stub = _reader(
        {
            "legible": True,
            "kind": "SAP posting confirmation",
            "period": "FY26P07",
            "entity": "FR01",
            "currency": "EUR",
            "amounts": [{"label": "JE total", "value": "27905812.64"}],
            "approval_seen": True,
        }
    )

    reading, response = await read_picture(stub, prompts, _picture())

    assert reading is not None
    assert reading.picture_id == "P1"
    assert reading.legible
    assert reading.kind == "SAP posting confirmation"
    assert reading.amounts == (("JE total", Decimal("27905812.64")),)
    assert reading.approval_seen
    assert response is not None


async def test_an_illegible_screenshot_is_a_real_answer(prompts: PromptRegistry) -> None:
    stub = _reader({"legible": False, "kind": None, "amounts": [], "approval_seen": False})

    reading, _ = await read_picture(stub, prompts, _picture())

    assert reading is not None
    assert not reading.legible
    assert reading.amounts == ()


async def test_a_grouped_amount_is_read_as_a_number(prompts: PromptRegistry) -> None:
    stub = _reader(
        {
            "legible": True,
            "kind": "x",
            "amounts": [{"label": "Hash total", "value": "27,905,812.64"}],
            "approval_seen": False,
        }
    )

    reading, _ = await read_picture(stub, prompts, _picture())

    assert reading is not None
    assert reading.amounts == (("Hash total", Decimal("27905812.64")),)


async def test_an_amount_written_as_prose_is_dropped_not_coerced(
    prompts: PromptRegistry,
) -> None:
    """A zero standing in for "about four thousand" would reconcile cleanly against nothing."""
    stub = _reader(
        {
            "legible": True,
            "kind": "x",
            "amounts": [
                {"label": "JE total", "value": "about four thousand"},
                {"label": "Hash total", "value": "100.00"},
            ],
            "approval_seen": False,
        }
    )

    reading, _ = await read_picture(stub, prompts, _picture())

    assert reading is not None
    assert reading.amounts == (("Hash total", Decimal("100.00")),)


async def test_the_image_itself_is_sent_inline(prompts: PromptRegistry) -> None:
    """Inline rather than uploaded somewhere: a payroll screen gains no third surface."""
    stub = _reader({"legible": False, "kind": None, "amounts": [], "approval_seen": False})
    picture = _picture()

    await read_picture(stub, prompts, picture)

    request = stub.requests[0]
    assert request.image is not None
    assert request.image.content_type == "image/png"
    assert request.image.data == picture.data
    assert request.image.data_url.startswith("data:image/png;base64,")


async def test_the_text_sent_with_it_says_where_the_picture_sits_and_nothing_more(
    prompts: PromptRegistry,
) -> None:
    stub = _reader({"legible": False, "kind": None, "amounts": [], "approval_seen": False})

    await read_picture(stub, prompts, _picture())

    payload = stub.payload("picture")
    assert payload["where_this_picture_sits"] == "Batch JE!AD9"
    assert "score" not in payload


def test_the_schema_requires_the_legibility_answer() -> None:
    assert "legible" in PICTURE_SCHEMA["required"]
    assert PICTURE_SCHEMA["additionalProperties"] is False
