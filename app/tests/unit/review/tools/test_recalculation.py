"""The recalculation as a tool: the figures a check's status is actually set from.

The engine's own measurements are tested next door. What matters here is that they reach the
routing rules as ordinary figures - decisive on the entry, coverage on the rows - so nothing
downstream needs to know this check is special.
"""

from decimal import Decimal

from review.models.context import BookedSpec, RecalcInputSpec, RecalculationSpec
from review.models.verdicts import Status
from review.models.workbook_data import CellValue
from review.tools.base import ToolContext, run_tool
from review.tools.recalculation import TOOL_NAME, RecalcEntryTotalTool, run_recalculation
from tests.unit.review.tools.conftest import make_context

CALCULATION = ("employee_ref", "fte_accrual", "non_fte_accrual", "total_accrual")
POSTING = ("company_code", "account", "currency", "line_amount")

SPEC = RecalculationSpec(
    table="calculation",
    record_key="employee_ref",
    stated="total_accrual",
    formula="fte_amount + non_fte_amount",
    booked=BookedSpec(),
    inputs=(
        RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
        RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
    ),
)


def context(records: list[list[CellValue]], posted: Decimal) -> ToolContext:
    ctx = make_context(
        {
            "calculation": (CALCULATION, records),
            "je_posting": (POSTING, [["FR01", "720008", "EUR", posted]]),
        }
    )
    ctx.je_type_args["recalculation"] = SPEC
    return ctx


TIES: list[list[CellValue]] = [
    ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
    ["E2", Decimal("150.00"), Decimal("50.00"), Decimal("200.00")],
]


def test_the_entry_figure_is_the_decisive_one() -> None:
    _, figures = run_recalculation(context(TIES, Decimal("300.00")), SPEC, "entry_total")

    decisive = [figure for figure in figures if figure.decisive]
    assert len(decisive) == 1, "two figures competing to be decisive is a status nobody chose"
    assert decisive[0].ok is True
    assert decisive[0].supports_pass is True
    assert Decimal("300.00") in decisive[0].numbers


def test_the_record_level_figure_carries_the_coverage() -> None:
    """Gate rule 1 lives in ``Figure.supports_pass``; this check must feed it like any other."""
    records: list[list[CellValue]] = [*TIES, ["E3", None, Decimal("10.00"), Decimal("10.00")]]
    _, figures = run_recalculation(context(records, Decimal("300.00")), SPEC, "entry_total")

    rows = next(figure for figure in figures if figure.coverage is not None)
    assert rows.decisive is False
    assert rows.coverage is not None
    assert rows.coverage.rows_total == 3
    assert rows.coverage.rows_bound == 2
    assert rows.coverage.complete is False
    assert rows.supports_pass is False


def test_a_difference_is_priced_so_findings_rank_by_money() -> None:
    _, figures = run_recalculation(context(TIES, Decimal("250.00")), SPEC, "entry_total")

    entry = next(figure for figure in figures if figure.decisive)
    assert entry.ok is False
    assert entry.financial_impact == Decimal("50.00")
    assert entry.samples[0]["booked"] == Decimal("250.00")
    assert entry.samples[0]["recomputed"] == Decimal("300.00")


def test_every_number_the_report_may_quote_is_on_the_figure() -> None:
    """Step 19 lists any number in the prose that traces to no figure. These must trace."""
    paper, figures = run_recalculation(context(TIES, Decimal("250.00")), SPEC, "entry_total")

    assert paper is not None
    quotable = {number for figure in figures for number in figure.numbers}
    assert paper.recomputed_total in quotable
    assert paper.booked.total in quotable
    assert paper.difference in quotable
    assert abs(paper.difference or Decimal(0)) in quotable


def test_an_entry_type_with_no_approved_formula_cannot_be_recalculated() -> None:
    paper, figures = run_recalculation(context(TIES, Decimal("300.00")), None, "entry_total")

    assert paper is None
    assert len(figures) == 1
    assert figures[0].ok is None
    assert figures[0].supports_pass is False
    assert "no approved formula" in figures[0].summary


def test_a_working_the_map_never_bound_is_unable_to_validate_not_a_failure() -> None:
    ctx = make_context({"je_posting": (POSTING, [["FR01", "720008", "EUR", Decimal("1.00")]])})
    ctx.je_type_args["recalculation"] = SPEC

    paper, figures = run_recalculation(ctx, SPEC, "entry_total")

    assert paper is None
    assert figures[0].ok is None
    assert "calculation" in (figures[0].error or "")


def test_the_tool_reads_its_basis_from_the_entry_type_and_not_from_the_plan() -> None:
    """A tool argument is a column or a table. A methodology travelling in a plan is not."""
    ctx = context(TIES, Decimal("300.00"))

    figures = run_tool(RecalcEntryTotalTool(), ctx, "entry_total", table="calculation")

    assert figures[0].ok is True
    assert RecalcEntryTotalTool().name == TOOL_NAME
    assert set(RecalcEntryTotalTool().signature) == {"table"}


def test_the_table_argument_is_the_table_that_is_rebuilt() -> None:
    """The recipe names it and step 12 verifies it is bound, so the tool must honour it."""
    ctx = make_context(
        {
            "calculation": (CALCULATION, TIES),
            "employee_master": (
                CALCULATION,
                [["E9", Decimal("7.00"), Decimal("0.00"), Decimal("7.00")]],
            ),
            "je_posting": (POSTING, [["FR01", "720008", "EUR", Decimal("7.00")]]),
        }
    )

    paper, _ = run_recalculation(ctx, SPEC, "entry_total", table="employee_master")

    assert paper is not None
    assert paper.basis.table == "employee_master"
    assert paper.recomputed_total == Decimal("7.00")
    assert paper.status is Status.PASS
