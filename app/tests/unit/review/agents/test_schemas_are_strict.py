"""Every schema in the harness must satisfy the provider's strict contract.

A strict ``json_schema`` is refused unless **every** key in ``properties`` also appears in
``required``, and unless ``additionalProperties`` is false on every object. A schema that
breaks either rule does not fail at import, in review, or in any test of its own module — it
fails with a 400 the first time that call actually fires.

Three of ours broke it. AI-R was caught by a live run; AI-X and AI-2 had simply never fired.
This file is the test that would have caught all three, and it covers every schema at once so
the next one cannot slip through either.
"""

from typing import Any

import pytest

from review.agents.clustering import CLUSTERS_SCHEMA
from review.agents.methodology import METHODOLOGY_SCHEMA
from review.agents.picture import PICTURE_SCHEMA
from review.agents.schemas import CHALLENGES_SCHEMA, VERDICTS_SCHEMA
from review.agents.summary import SUMMARY_SCHEMA
from review.agents.understand import UNDERSTANDING_SCHEMA
from review.orchestrator.planner import PLAN_SCHEMA

SCHEMAS: dict[str, dict[str, Any]] = {
    "AI-1 understanding": UNDERSTANDING_SCHEMA,
    "AI-2 plan": PLAN_SCHEMA,
    "AI-3 verdicts": VERDICTS_SCHEMA,
    "AI-4 challenges": CHALLENGES_SCHEMA,
    "AI-5 summary": SUMMARY_SCHEMA,
    "AI-M methodology": METHODOLOGY_SCHEMA,
    "AI-R picture": PICTURE_SCHEMA,
    "AI-X clusters": CLUSTERS_SCHEMA,
}


def _objects(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every object schema inside one schema, including the nested ones."""
    found: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(node, dict):
        return found

    if node.get("type") == "object" or "properties" in node:
        found.append((path or "(root)", node))

    for key, child in node.items():
        if key in {"properties", "items", "$defs", "definitions"}:
            if isinstance(child, dict) and key in {"items"}:
                found.extend(_objects(child, f"{path}[]"))
            elif isinstance(child, dict):
                for name, value in child.items():
                    found.extend(_objects(value, f"{path}.{name}" if path else name))
    return found


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_property_is_required(name: str) -> None:
    """Strict mode has no optional keys. A field with no answer uses a null type."""
    for path, node in _objects(SCHEMAS[name]):
        properties = set(node.get("properties", {}))
        if not properties:
            continue
        required = set(node.get("required", []))
        assert properties == required, (
            f"{name} at {path}: required must list every property. "
            f"Missing {sorted(properties - required)}; "
            f"unknown {sorted(required - properties)}"
        )


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_no_object_accepts_extra_keys(name: str) -> None:
    """A model that can add a key can add one the parser silently ignores."""
    for path, node in _objects(SCHEMAS[name]):
        if not node.get("properties"):
            continue
        assert node.get("additionalProperties") is False, (
            f"{name} at {path}: additionalProperties must be false under strict mode"
        )


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_the_root_is_an_object(name: str) -> None:
    """The provider returns one object per call; a bare array has nowhere to grow."""
    assert SCHEMAS[name]["type"] == "object"


def test_every_model_call_in_the_harness_is_covered_here() -> None:
    """The guard is only a guard if a new call cannot be added without joining it."""
    from api.consts import AICall

    # AI-M, AI-R and AI-X are conditional; every call the enum names has a schema here.
    assert len(SCHEMAS) == len(AICall), (
        f"{len(AICall)} model calls exist and {len(SCHEMAS)} schemas are checked. "
        "A new call needs its schema added to SCHEMAS above."
    )
