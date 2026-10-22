"""The three arithmetic tools, and the contract every tool keeps."""

from decimal import Decimal

import pytest

from review.models.evidence import Figure
from review.models.workbook_data import CellValue
from review.tools.base import BaseTool, ToolContext, ToolError, assign_figure_ids, run_tool
from review.tools.calculation import BalanceTool, ControlTotalsTool, RollupChainTool
from tests.unit.review.tools.conftest import make_context

# ----------------------------------------------------------------------- the tool contract


class _Exploding(BaseTool):
    name = "exploding"

    def run(self, ctx: ToolContext, check_id: str, **args: object) -> list[Figure]:
        raise ZeroDivisionError("denominator was zero")


class _Unbound(BaseTool):
    name = "unbound"

    def run(self, ctx: ToolContext, check_id: str, **args: object) -> list[Figure]:
        raise ToolError("no column bound to 'debit'")


def test_an_arithmetic_failure_becomes_unable_to_validate(balanced_posting: ToolContext) -> None:
    """A tool that raises must not fail the whole review for one bad column."""
    figures = run_tool(_Exploding(), balanced_posting, "some_check")
    assert len(figures) == 1
    assert figures[0].ok is None
    assert figures[0].supports_pass is False
    assert "ZeroDivisionError" in (figures[0].error or "")


def test_a_missing_binding_becomes_unable_to_validate(balanced_posting: ToolContext) -> None:
    figures = run_tool(_Unbound(), balanced_posting, "some_check")
    assert figures[0].ok is None
    assert "debit" in (figures[0].error or "")


def test_figure_ids_follow_plan_order(balanced_posting: ToolContext) -> None:
    figures = BalanceTool().run(balanced_posting, "entry_balances") * 3
    numbered = assign_figure_ids(figures)
    assert [figure.figure_id for figure in numbered] == ["F-01", "F-02", "F-03"]


class _Chatty(BaseTool):
    """Returns far more exemplars than any deployment shows a reader."""

    name = "chatty"
    SAMPLES = 25

    def run(self, ctx: ToolContext, check_id: str, **args: object) -> list[Figure]:
        return [
            self.figure(
                check_id,
                ok=False,
                summary="25 rows out",
                exception_count=self.SAMPLES,
                samples=[{"row": row, "difference": Decimal(row)} for row in range(self.SAMPLES)],
            )
        ]


def test_run_tool_caps_samples_at_the_configured_limit() -> None:
    """``config.FIGURE_SAMPLES`` reaches a figure, and only the exemplars are capped."""
    ctx = make_context({"je_posting": (("account",), [["720008"]])}, figure_samples=3)
    figures = run_tool(_Chatty(), ctx, "some_check")
    assert [sample["row"] for sample in figures[0].samples] == [0, 1, 2]
    assert figures[0].exception_count == _Chatty.SAMPLES


def test_run_tool_leaves_samples_alone_under_the_limit(balanced_posting: ToolContext) -> None:
    figures = run_tool(_Chatty(), balanced_posting, "some_check")
    assert len(figures[0].samples) == balanced_posting.figure_samples


def test_a_tool_never_sees_anything_a_model_said(balanced_posting: ToolContext) -> None:
    """The evidence pack is a product of arithmetic, so the context carries no verdicts."""
    assert not hasattr(balanced_posting, "verdicts")
    assert not hasattr(balanced_posting, "sheet_map_rationale")


# ------------------------------------------------------------------------------- balance


def test_a_balanced_entry_passes(balanced_posting: ToolContext) -> None:
    figure = BalanceTool().run(
        balanced_posting, "entry_balances", group_by=["company_code", "currency"]
    )[0]
    assert figure.ok is True
    assert figure.exception_count == 0


def test_a_grouped_imbalance_is_caught_even_when_the_total_nets_to_zero() -> None:
    """The error grouping exists for: two company codes out by equal and opposite amounts."""
    ctx = make_context(
        {
            "je_posting": (
                ("company_code", "line_amount"),
                [
                    ["FR01", Decimal("100.00")],
                    ["FR02", Decimal("-100.00")],
                ],
            )
        }
    )
    ungrouped = BalanceTool().run(ctx, "entry_balances")[0]
    grouped = BalanceTool().run(ctx, "entry_balances", group_by=["company_code"])[0]

    assert ungrouped.ok is True, "the file as a whole nets to zero"
    assert grouped.ok is False, "but neither company code does"
    assert grouped.exception_count == 2
    assert grouped.financial_impact == Decimal("200.00")


def test_rounding_within_tolerance_is_not_an_exception() -> None:
    ctx = make_context(
        {"je_posting": (("company_code", "line_amount"), [["FR01", Decimal("0.004")]])}
    )
    assert BalanceTool().run(ctx, "entry_balances")[0].ok is True


def test_a_difference_above_tolerance_is_an_exception() -> None:
    ctx = make_context(
        {"je_posting": (("company_code", "line_amount"), [["FR01", Decimal("0.02")]])}
    )
    assert BalanceTool().run(ctx, "entry_balances")[0].ok is False


def test_an_unreadable_amount_is_counted_not_treated_as_zero() -> None:
    """A blank or error cell is a gap. Reading it as zero would turn it into a clean tie."""
    ctx = make_context(
        {
            "je_posting": (
                ("company_code", "line_amount"),
                [["FR01", Decimal("50.00")], ["FR02", "#VALUE!"], ["FR03", Decimal("-50.00")]],
            )
        }
    )
    figure = BalanceTool().run(ctx, "entry_balances")[0]
    assert figure.exception_count == 1
    assert "1 line(s) held no readable amount" in figure.summary


# ------------------------------------------------------------------------ control totals


def test_a_hash_total_matching_the_gross_sum_passes(balanced_posting: ToolContext) -> None:
    """A hash total is the sum of absolute amounts, not the net, and both readings are tried."""
    figures = ControlTotalsTool().run(balanced_posting, "stated_totals", facts=["hash_total"])
    assert figures[0].ok is True


def test_a_je_total_matching_the_net_passes(balanced_posting: ToolContext) -> None:
    figures = ControlTotalsTool().run(balanced_posting, "stated_totals", facts=["je_total"])
    assert figures[0].ok is True


def test_a_stale_stated_total_is_an_exception_with_its_money() -> None:
    ctx = make_context(
        {"je_posting": (("line_amount",), [[Decimal("100.00")], [Decimal("50.00")]])},
        facts={"hash_total": Decimal("125.00")},
    )
    figure = ControlTotalsTool().run(ctx, "stated_totals", facts=["hash_total"])[0]
    assert figure.ok is False
    assert figure.financial_impact == Decimal("25.00")
    assert Decimal("125.00") in figure.numbers


def test_a_total_that_is_not_stated_cannot_be_compared() -> None:
    """Unable to validate, not a failure: nobody claimed anything to disagree with."""
    ctx = make_context({"je_posting": (("line_amount",), [[Decimal("1.00")]])})
    figure = ControlTotalsTool().run(ctx, "stated_totals", facts=["hash_total"])[0]
    assert figure.ok is None
    assert figure.decisive is False


# --------------------------------------------------------------------------- roll-up chain


def _chain(calc: CellValue, agg: CellValue, post: CellValue) -> ToolContext:
    return make_context(
        {
            "calculation": (("company_code", "total_accrual"), [["FR01", calc]]),
            "aggregation": (("company_code", "fte_accrual"), [["FR01", agg]]),
            "je_posting": (("company_code", "line_amount"), [["FR01", post]]),
        }
    )


def test_a_chain_that_carries_the_same_money_passes() -> None:
    figures = RollupChainTool().run(
        _chain(Decimal("100"), Decimal("100"), Decimal("100")),
        "working_ties",
        hops=["calculation", "aggregation", "je_posting"],
    )
    assert len(figures) == 2, "one figure per hop, not one for the chain"
    assert all(figure.ok for figure in figures)


def test_two_hops_wrong_in_opposite_directions_are_both_caught() -> None:
    """The reason hops are checked individually: the grand total still agrees."""
    figures = RollupChainTool().run(
        _chain(Decimal("100"), Decimal("130"), Decimal("100")),
        "working_ties",
        hops=["calculation", "aggregation", "je_posting"],
    )
    assert [figure.ok for figure in figures] == [False, False]
    assert figures[0].financial_impact == Decimal("30")
    assert figures[1].financial_impact == Decimal("30")


def test_a_missing_hop_is_reported_rather_than_skipped() -> None:
    ctx = make_context(
        {
            "calculation": (("company_code", "total_accrual"), [["FR01", Decimal("100")]]),
            "je_posting": (("company_code", "line_amount"), [["FR01", Decimal("100")]]),
        }
    )
    figures = RollupChainTool().run(
        ctx, "working_ties", hops=["calculation", "aggregation", "je_posting"]
    )
    unresolved = [figure for figure in figures if figure.ok is None]
    assert unresolved, "a hop that could not be validated must say so"
    assert "aggregation" in unresolved[0].summary


def test_a_chain_needs_two_tables() -> None:
    with pytest.raises(ToolError, match="at least two"):
        RollupChainTool().run(
            _chain(Decimal(1), Decimal(1), Decimal(1)), "working_ties", hops=["calculation"]
        )


def test_a_group_on_only_one_side_is_an_exception() -> None:
    ctx = make_context(
        {
            "calculation": (
                ("company_code", "total_accrual"),
                [["FR01", Decimal("100")], ["FR02", Decimal("40")]],
            ),
            "je_posting": (("company_code", "line_amount"), [["FR01", Decimal("100")]]),
        }
    )
    figure = RollupChainTool().run(ctx, "working_ties", hops=["calculation", "je_posting"])[0]
    assert figure.ok is False
    assert figure.financial_impact == Decimal("40")
    assert any(sample["one_sided"] for sample in figure.samples)
