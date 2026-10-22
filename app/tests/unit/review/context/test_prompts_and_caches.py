"""The prompt registry and the two caches.

Both exist to make a review cheaper without making it different, so most of what is worth
testing is what *must* invalidate them.
"""

from pathlib import Path

import pytest

from api.consts import AICall
from review.context.caches import LayoutCache, LayoutEntry, PictureCache
from review.context.loader import ContextError
from review.context.prompts import STATIC_BLOCK, PromptRegistry
from review.models.evidence import PictureReading
from review.models.plan import ExecutionPlan
from review.models.sheet_map import SheetMap

# --------------------------------------------------------------------- the prompt registry


@pytest.fixture
def prompt_dir(tmp_path: Path) -> Path:
    for call in (AICall.JUDGE, AICall.CHALLENGE):
        directory = tmp_path / call.value
        directory.mkdir()
        (directory / "v1.md").write_text(f"First {call.value}.", encoding="utf-8")
    return tmp_path


def test_the_static_block_leads_every_prompt(prompt_dir: Path) -> None:
    """It holds the rules that outrank the step, and leading with it is also what makes the
    provider's prefix cache hit across reviews."""
    registry = PromptRegistry(prompt_dir)
    registry.load()
    assert registry.get(AICall.JUDGE).text.startswith(STATIC_BLOCK)


def test_the_highest_version_wins(prompt_dir: Path) -> None:
    (prompt_dir / AICall.JUDGE.value / "v2.md").write_text("Second.", encoding="utf-8")
    (prompt_dir / AICall.JUDGE.value / "v10.md").write_text("Tenth.", encoding="utf-8")
    registry = PromptRegistry(prompt_dir)
    registry.load()

    prompt = registry.get(AICall.JUDGE)
    assert prompt.version == 10, "v10 must beat v2, not sort before it as a string"
    assert "Tenth." in prompt.text


def test_older_versions_stay_readable(prompt_dir: Path) -> None:
    """A reviewer reopening a six-month-old result needs the instructions it ran under."""
    (prompt_dir / AICall.JUDGE.value / "v2.md").write_text("Second.", encoding="utf-8")
    PromptRegistry(prompt_dir).load()
    assert (prompt_dir / AICall.JUDGE.value / "v1.md").is_file()


def test_changing_a_prompt_changes_its_digest(prompt_dir: Path) -> None:
    first = PromptRegistry(prompt_dir)
    first.load()
    before = first.get(AICall.JUDGE).sha

    (prompt_dir / AICall.JUDGE.value / "v1.md").write_text("Reworded.", encoding="utf-8")
    second = PromptRegistry(prompt_dir)
    second.load()
    assert second.get(AICall.JUDGE).sha != before


def test_version_strings_are_recorded_per_call(prompt_dir: Path) -> None:
    registry = PromptRegistry(prompt_dir)
    registry.load()
    assert set(registry.versions) == {AICall.JUDGE.value, AICall.CHALLENGE.value}
    assert registry.versions[AICall.JUDGE.value].startswith("v1#")


def test_a_missing_required_prompt_is_a_startup_error(prompt_dir: Path) -> None:
    with pytest.raises(ContextError, match="No prompt file"):
        PromptRegistry(prompt_dir).load(required=frozenset({AICall.SUMMARY}))


def test_asking_for_an_unloaded_prompt_raises(prompt_dir: Path) -> None:
    registry = PromptRegistry(prompt_dir)
    registry.load()
    with pytest.raises(ContextError, match="No prompt loaded"):
        registry.get(AICall.SUMMARY)


def test_the_shipped_prompts_all_load() -> None:
    registry = PromptRegistry()
    registry.load(required=frozenset(AICall))
    assert len(registry.versions) == len(AICall)


# ------------------------------------------------------------------------- the layout cache


def _entry() -> LayoutEntry:
    return LayoutEntry(sheet_map=SheetMap(), plan=ExecutionPlan(), je_type="demo")


def _key(**overrides: str) -> str:
    args = {
        "layout_fingerprint": "f" * 64,
        "je_type_version": "demo@1#aaaa",
        "understand_prompt": "v1#bbbb",
        "plan_prompt": "v1#cccc",
        "model": "gpt-5.5",
    }
    args.update(overrides)
    return LayoutCache.key(**args)


def test_a_clean_map_round_trips() -> None:
    cache = LayoutCache()
    key = _key()
    assert cache.put(key, _entry(), map_problems=0) is True
    assert cache.get(key) is not None


def test_a_map_that_needed_repair_is_never_cached() -> None:
    """Caching an unconfirmed reading spreads one bad map across every future file from
    that template."""
    cache = LayoutCache()
    key = _key()
    assert cache.put(key, _entry(), map_problems=1) is False
    assert cache.get(key) is None


@pytest.mark.parametrize(
    "field",
    ["layout_fingerprint", "je_type_version", "understand_prompt", "plan_prompt", "model"],
)
def test_every_version_in_the_key_invalidates_it(field: str) -> None:
    """A reworded prompt can produce a different map from the same workbook, and serving
    the stored one would hide that."""
    assert _key(**{field: "changed"}) != _key()


def test_the_key_cannot_be_forged_across_fields() -> None:
    """Fields are length-prefixed, so two different splits cannot share a key."""
    assert _key(je_type_version="a", understand_prompt="b") != _key(
        je_type_version="a|b", understand_prompt=""
    )


def test_an_idle_entry_expires() -> None:
    cache = LayoutCache(ttl_days=0)
    key = _key()
    cache.put(key, _entry(), map_problems=0)
    assert cache.get(key) is None
    assert len(cache) == 0, "an expired entry is dropped on read, not left to accumulate"


def test_eviction_reports_what_it_dropped() -> None:
    cache = LayoutCache(ttl_days=0)
    cache.put(_key(), _entry(), map_problems=0)
    assert cache.evict_expired() == 1
    assert cache.evict_expired() == 0


# ------------------------------------------------------------------------ the picture cache


def test_a_picture_reading_round_trips() -> None:
    cache = PictureCache()
    key = PictureCache.key("a" * 64, reader_prompt="v1#dddd")
    cache.put(key, PictureReading(picture_id="P1", legible=True))
    stored = cache.get(key)
    assert stored is not None
    assert stored.picture_id == "P1"


def test_a_reworded_reader_prompt_invalidates_the_picture() -> None:
    first = PictureCache.key("a" * 64, reader_prompt="v1#dddd")
    second = PictureCache.key("a" * 64, reader_prompt="v2#eeee")
    assert first != second


def test_a_different_picture_is_a_different_key() -> None:
    first = PictureCache.key("a" * 64, reader_prompt="v1#dddd")
    second = PictureCache.key("b" * 64, reader_prompt="v1#dddd")
    assert first != second
