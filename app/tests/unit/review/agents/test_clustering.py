"""Step 13b: grouping exceptions by cause, and the limits on what that can change.

Nothing here may alter a status, a count or an amount. The tests are mostly about that: a
cluster citing a figure it was never given is dropped, groups that do not add up are
reported rather than corrected, and a review below the threshold never pays for the call.
"""

from decimal import Decimal

import pytest

from review.agents.clustering import (
    CLUSTERS_SCHEMA,
    cluster,
    needs_clustering,
    reconciles,
)
from review.context.prompts import PromptRegistry
from review.models.context import ClusteringRules
from review.models.evidence import ExceptionCluster, Figure
from tests.doubles import StubLLMProvider

pytestmark = pytest.mark.anyio

RULES = ClusteringRules(min_exceptions=25, max_clusters=6)


def _figure(figure_id: str, check_id: str, exceptions: int) -> Figure:
    return Figure(
        figure_id=figure_id,
        check_id=check_id,
        tool="recalc_leave_balance",
        ok=False,
        decisive=True,
        exception_count=exceptions,
        financial_impact=Decimal("1000.00"),
        summary=f"{exceptions} rows did not agree",
        numbers=(Decimal(exceptions),),
    )


def _pack(exceptions: int = 888) -> dict[str, tuple[Figure, ...]]:
    return {
        "leave_balance_recalculates": (_figure("F-01", "leave_balance_recalculates", exceptions),)
    }


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.load()
    return registry


def _answer(*clusters: dict[str, object]) -> StubLLMProvider:
    return StubLLMProvider({"clusters": {"clusters": list(clusters)}})


# --- the threshold ------------------------------------------------------------------------


def test_a_crowded_check_needs_grouping() -> None:
    assert needs_clustering(_pack()["leave_balance_recalculates"], RULES)


def test_a_handful_of_exceptions_does_not() -> None:
    """Grouping six rows into three causes costs a call and tells a reviewer nothing."""
    assert not needs_clustering(_pack(6)["leave_balance_recalculates"], RULES)


def test_the_threshold_is_inclusive() -> None:
    assert needs_clustering(_pack(25)["leave_balance_recalculates"], RULES)


async def test_a_review_under_the_threshold_never_calls_the_model(prompts: PromptRegistry) -> None:
    stub = _answer()

    clusters, response = await cluster(stub, prompts, _pack(6), RULES)

    assert clusters == {}
    assert response is None
    assert stub.requests == []


async def test_no_provider_means_no_clusters(prompts: PromptRegistry) -> None:
    clusters, response = await cluster(None, prompts, _pack(), RULES)

    assert clusters == {}
    assert response is None


# --- reading the answer -------------------------------------------------------------------


async def test_a_cause_is_attached_to_the_check_its_figure_belongs_to(
    prompts: PromptRegistry,
) -> None:
    stub = _answer(
        {
            "cause": "137 rows where earned leave is a hardcoded zero",
            "affected": 137,
            "financial_impact": "4200.00",
            "figure_ids": ["F-01"],
            "example_rows": [35, 36, 37],
        }
    )

    clusters, response = await cluster(stub, prompts, _pack(), RULES)

    found = clusters["leave_balance_recalculates"]
    assert len(found) == 1
    assert found[0].cause.startswith("137 rows")
    assert found[0].affected == 137
    assert found[0].financial_impact == Decimal("4200.00")
    assert found[0].example_rows == (35, 36, 37)
    assert response is not None


async def test_a_cluster_citing_a_figure_it_was_never_given_is_dropped(
    prompts: PromptRegistry,
) -> None:
    """The same unverified citation ``answer_check`` catches on a verdict."""
    stub = _answer(
        {"cause": "something", "affected": 10, "figure_ids": ["F-99"], "example_rows": []}
    )

    clusters, _ = await cluster(stub, prompts, _pack(), RULES)

    assert clusters == {}


async def test_a_negative_count_cannot_come_back(prompts: PromptRegistry) -> None:
    stub = _answer({"cause": "x", "affected": -5, "figure_ids": ["F-01"], "example_rows": []})

    clusters, _ = await cluster(stub, prompts, _pack(), RULES)

    assert clusters["leave_balance_recalculates"][0].affected == 0


async def test_an_unparseable_amount_becomes_no_amount_rather_than_a_wrong_one(
    prompts: PromptRegistry,
) -> None:
    stub = _answer(
        {
            "cause": "x",
            "affected": 10,
            "financial_impact": "about four thousand",
            "figure_ids": ["F-01"],
            "example_rows": [],
        }
    )

    clusters, _ = await cluster(stub, prompts, _pack(), RULES)

    assert clusters["leave_balance_recalculates"][0].financial_impact is None


async def test_more_causes_than_the_business_allows_are_truncated(prompts: PromptRegistry) -> None:
    stub = _answer(
        *(
            {"cause": f"cause {n}", "affected": 1, "figure_ids": ["F-01"], "example_rows": []}
            for n in range(10)
        )
    )

    clusters, _ = await cluster(stub, prompts, _pack(), RULES)

    assert len(clusters["leave_balance_recalculates"]) == RULES.max_clusters


async def test_the_model_sees_only_tool_output(prompts: PromptRegistry) -> None:
    stub = _answer()

    await cluster(stub, prompts, _pack(), RULES)

    payload = stub.payload("clusters")
    assert set(payload) == {"at_most_this_many_causes", "checks"}
    assert payload["checks"][0]["total_exceptions"] == 888
    # No score, no weights, no status: none of those are its business.
    assert "score" not in payload


# --- reconciliation -----------------------------------------------------------------------


def test_causes_that_account_for_every_exception_reconcile() -> None:
    figures = _pack(100)["leave_balance_recalculates"]
    clusters = (
        ExceptionCluster(cause="a", affected=60, figure_ids=("F-01",)),
        ExceptionCluster(cause="b", affected=40, figure_ids=("F-01",)),
    )

    assert reconciles(clusters, figures)


def test_causes_that_leave_exceptions_unaccounted_for_do_not() -> None:
    """Reported, not corrected: the groups are still worth reading."""
    figures = _pack(100)["leave_balance_recalculates"]
    clusters = (ExceptionCluster(cause="a", affected=60, figure_ids=("F-01",)),)

    assert not reconciles(clusters, figures)


def test_the_schema_forbids_fields_nobody_asked_for() -> None:
    assert CLUSTERS_SCHEMA["additionalProperties"] is False
    item = CLUSTERS_SCHEMA["properties"]["clusters"]["items"]
    assert item["additionalProperties"] is False
    assert "status" not in item["properties"], "a cluster never carries a status"
