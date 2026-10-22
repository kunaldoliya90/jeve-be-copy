"""The context loader: what it accepts, and what it refuses to start with.

Every refusal here is a rule that would otherwise have been applied silently and wrongly to
a real review, so each test names the consequence rather than the schema.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from review.context.loader import ContextError, ContextLoader
from review.models.context import DecidedBy

MINIMAL_RULES: dict[str, Any] = {
    "version": 1,
    "credits": {
        "pass": 100,
        "partial": 50,
        "unable_to_validate": 50,
        "missing_evidence": 0,
        "fail": 0,
    },
    "bands": [
        {"min": 51, "label": "Ok", "risk": "low"},
        {"min": 0, "label": "Risk", "risk": "high"},
    ],
}

MINIMAL_TYPE: dict[str, Any] = {
    "id": "demo",
    "version": 1,
    "name": "Demo",
    "objective": "Prove something.",
    "areas": {"period": {"weight": 100, "method": "weakest_check"}},
    "checks": [
        {
            "id": "dates_agree",
            "area": "period",
            "question": "Do the dates agree?",
            "decided_by": "judgement",
        }
    ],
}


@pytest.fixture
def context_dir(tmp_path: Path) -> Path:
    (tmp_path / "je_types").mkdir()
    _write(tmp_path / "business_rules.yaml", MINIMAL_RULES)
    _write(tmp_path / "je_types" / "demo.yaml", MINIMAL_TYPE)
    return tmp_path


def _write(path: Path, payload: Any) -> None:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _loader(root: Path) -> ContextLoader:
    return ContextLoader(je_types_dir=root / "je_types", rules_file=root / "business_rules.yaml")


def _load(root: Path, je_type: dict[str, Any]) -> ContextLoader:
    _write(root / "je_types" / "demo.yaml", je_type)
    loader = _loader(root)
    loader.load()
    return loader


# ------------------------------------------------------------------------ the happy path


def test_loads_rules_and_types(context_dir: Path) -> None:
    loader = _loader(context_dir)
    loader.load()
    assert loader.rules.version == 1
    assert loader.je_type("demo").name == "Demo"


def test_versions_carry_the_file_digest(context_dir: Path) -> None:
    """The digest is what stops a stored result being served across an edit."""
    loader = _loader(context_dir)
    loader.load()
    before = loader.je_type_version("demo")

    _write(context_dir / "je_types" / "demo.yaml", {**MINIMAL_TYPE, "objective": "Changed."})
    after_loader = _loader(context_dir)
    after_loader.load()
    assert after_loader.je_type_version("demo") != before


def test_the_real_shipped_context_is_valid() -> None:
    """The files that actually ship, loaded exactly as the application loads them."""
    loader = ContextLoader()
    loader.load()
    france = loader.je_type("vacation-accrual-france")
    assert sum(area.weight for area in france.areas.values()) == 100
    assert france.discriminators is not None
    assert france.methodology is not None
    assert france.methodology.salary_per_day_denominator != "30"


# ------------------------------------------------------------------- refusals at startup


def test_area_weights_must_total_one_hundred(context_dir: Path) -> None:
    """Weights that do not sum to 100 make every score silently out of scale."""
    broken = {**MINIMAL_TYPE, "areas": {"period": {"weight": 90, "method": "weakest_check"}}}
    with pytest.raises(ContextError, match="total 90"):
        _load(context_dir, broken)


def test_every_area_needs_at_least_one_check(context_dir: Path) -> None:
    """A weighted area with no check would contribute a score derived from nothing."""
    broken = {
        **MINIMAL_TYPE,
        "areas": {
            "period": {"weight": 50, "method": "weakest_check"},
            "orphan": {"weight": 50, "method": "weakest_check"},
        },
    }
    with pytest.raises(ContextError, match="no check"):
        _load(context_dir, broken)


def test_a_check_cannot_name_an_unscored_area(context_dir: Path) -> None:
    broken = {
        **MINIMAL_TYPE,
        "checks": [{**MINIMAL_TYPE["checks"][0], "area": "typo_area"}],
    }
    with pytest.raises(ContextError, match="unscored areas"):
        _load(context_dir, broken)


def test_duplicate_check_ids_are_refused(context_dir: Path) -> None:
    """Two checks with one id would overwrite each other's outcome in the case file."""
    broken = {**MINIMAL_TYPE, "checks": [MINIMAL_TYPE["checks"][0]] * 2}
    with pytest.raises(ContextError, match="duplicate check ids"):
        _load(context_dir, broken)


def test_a_figures_check_must_carry_a_recipe(context_dir: Path) -> None:
    """Figures-decided checks never use AI-2's plan, so without a recipe nothing runs."""
    broken = {
        **MINIMAL_TYPE,
        "checks": [{**MINIMAL_TYPE["checks"][0], "decided_by": DecidedBy.FIGURES.value}],
    }
    with pytest.raises(ContextError, match="no recipe"):
        _load(context_dir, broken)


def test_a_variant_cannot_require_and_reject_the_same_family(context_dir: Path) -> None:
    broken = {
        **MINIMAL_TYPE,
        "discriminators": {
            "precedence": 1,
            "variants": [{"id": "v", "requires_all": ["a", "b"], "rejects": ["b"]}],
        },
    }
    with pytest.raises(ContextError, match="both requires and rejects"):
        _load(context_dir, broken)


def test_two_types_cannot_share_a_precedence(context_dir: Path) -> None:
    """Order decides which of two near-matching types claims a borderline workbook."""
    with_discriminator = {
        **MINIMAL_TYPE,
        "discriminators": {
            "precedence": 5,
            "variants": [{"id": "v", "requires_all": ["a"]}],
        },
    }
    _write(context_dir / "je_types" / "demo.yaml", with_discriminator)
    _write(
        context_dir / "je_types" / "other.yaml",
        {**with_discriminator, "id": "other", "name": "Other"},
    )
    with pytest.raises(ContextError, match="share precedence"):
        _loader(context_dir).load()


def test_an_unknown_key_is_a_startup_error(context_dir: Path) -> None:
    """A typo in a hand-edited file must not read as a rule that quietly does nothing."""
    with pytest.raises(ContextError, match="toleranc"):
        _load(context_dir, {**MINIMAL_TYPE, "tolerancee": 1})


def test_bands_must_reach_zero(context_dir: Path) -> None:
    """Otherwise a low enough score matches no band at all."""
    _write(
        context_dir / "business_rules.yaml",
        {**MINIMAL_RULES, "bands": [{"min": 51, "label": "Ok", "risk": "low"}]},
    )
    with pytest.raises(ContextError, match="must start at 0"):
        _loader(context_dir).load()


def test_every_status_needs_a_credit(context_dir: Path) -> None:
    credits = dict(MINIMAL_RULES["credits"])
    del credits["partial"]
    _write(context_dir / "business_rules.yaml", {**MINIMAL_RULES, "credits": credits})
    with pytest.raises(ContextError, match="no credit defined"):
        _loader(context_dir).load()


def test_malformed_yaml_names_the_file(context_dir: Path) -> None:
    (context_dir / "je_types" / "demo.yaml").write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(ContextError, match="not valid YAML"):
        _loader(context_dir).load()


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "je_types").mkdir()
    _write(tmp_path / "business_rules.yaml", MINIMAL_RULES)
    with pytest.raises(ContextError, match="No JE type files"):
        _loader(tmp_path).load()


def test_an_unknown_je_type_lists_the_known_ones(context_dir: Path) -> None:
    loader = _loader(context_dir)
    loader.load()
    with pytest.raises(ContextError, match="Unknown JE type"):
        loader.je_type("nope")


def test_reading_before_loading_is_an_error(context_dir: Path) -> None:
    """Better than returning defaults that nobody chose."""
    with pytest.raises(ContextError, match="not loaded"):
        _ = _loader(context_dir).rules


# ------------------------------------------------------------------------- the catalogue


def test_the_catalogue_is_shallow_and_uniform(context_dir: Path) -> None:
    """AI-1 classifies blind against this. Describe one type more richly than the others and
    the model picks it every time, measuring description length rather than evidence."""
    _write(
        context_dir / "je_types" / "other.yaml",
        {**MINIMAL_TYPE, "id": "other", "name": "Other"},
    )
    loader = _loader(context_dir)
    loader.load()

    entries = loader.catalogue
    assert len(entries) == 2
    assert all(set(entry) == {"id", "name", "aliases", "objective"} for entry in entries)
    assert [entry["id"] for entry in entries] == ["demo", "other"]


def test_the_catalogue_never_carries_checks_or_tables(context_dir: Path) -> None:
    loader = _loader(context_dir)
    loader.load()
    serialised = str(loader.catalogue)
    assert "checks" not in serialised
    assert "discriminators" not in serialised


def test_a_score_gets_its_band_and_its_risk_rating_from_the_same_row(context_dir: Path) -> None:
    """The rating is the band, in one word. Two lookups that could disagree would put two
    numbers in the report that always agree except when they do not."""
    loader = _loader(context_dir)
    loader.load()
    rules = loader.rules

    for score in (100.0, 51.0, 50.9, 0.0):
        row = next(band for band in rules.bands if score >= band.min)
        assert rules.band_for(score) == row.label
        assert rules.risk_for(score) == row.risk
