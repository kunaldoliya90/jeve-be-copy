"""Step 13c on workbooks built to misbehave.

The real sample files all reconcile to the cent, which is the right outcome and useless as a
test of the interesting half. Everything here is a small workbook written to produce one
specific failure: a record that differs, an input that is missing, a booked side that cannot
be read, a column the map never bound.
"""

from decimal import Decimal

import pytest

from review.models.context import (
    BookedSpec,
    CurrencySpec,
    DerivedTermSpec,
    RecalcInputSpec,
    RecalculationSpec,
    RoundingSpec,
)
from review.models.recalculation import BookedMethod, DiscrepancyKind, RoundingMode
from review.models.verdicts import Status
from review.models.workbook_data import CellValue
from review.recalc.engine import CITATION_CAP, recalculate
from review.tools.base import ToolContext, ToolError
from tests.unit.review.tools.conftest import make_context

Records = list[list[CellValue]]

CALCULATION = ("employee_ref", "fte_accrual", "non_fte_accrual", "total_accrual")
POSTING = ("company_code", "account", "currency", "line_amount")


def spec(**overrides: object) -> RecalculationSpec:
    """The shape every test starts from: two legs added, tied to the posted debits."""
    base: dict[str, object] = {
        "table": "calculation",
        "record_key": "employee_ref",
        "stated": "total_accrual",
        "formula": "fte_amount + non_fte_amount",
        "tolerance_pct": Decimal("0.1"),
        "row_tolerance": Decimal("0.01"),
        "rounding": RoundingSpec(decimals=2, mode=RoundingMode.HALF_UP),
        "currency": CurrencySpec(default="EUR"),
        "booked": BookedSpec(),
        "inputs": (
            RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
            RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
        ),
    }
    base.update(overrides)
    return RecalculationSpec.model_validate(base)


def workbook(records: Records, posted: Records | None = None) -> ToolContext:
    lines: Records = (
        posted if posted is not None else [["FR01", "720008", "EUR", Decimal("300.00")]]
    )
    return make_context({"calculation": (CALCULATION, records), "je_posting": (POSTING, lines)})


# --------------------------------------------------------------------------- it reconciles


def test_an_entry_that_rebuilds_to_the_booked_amount_passes() -> None:
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
            ["E2", Decimal("150.00"), Decimal("50.00"), Decimal("200.00")],
        ]
    )

    paper = recalculate(ctx, spec())

    assert paper.status is Status.PASS
    assert paper.recomputed_total == Decimal("300.00")
    assert paper.booked.total == Decimal("300.00")
    assert paper.difference == Decimal("0.00")
    assert paper.within_tolerance is True
    assert paper.ties is True
    assert paper.complete is True
    assert paper.citations == ()
    assert "to the cent" in paper.explanation


def test_the_blank_and_subtotal_rows_beneath_the_data_are_not_records() -> None:
    """The row below the last employee restates the whole entry on one real workbook."""
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
            ["E2", Decimal("150.00"), Decimal("50.00"), Decimal("200.00")],
            [None, Decimal("250.00"), Decimal("50.00"), Decimal("300.00")],
        ]
    )

    paper = recalculate(ctx, spec())

    assert paper.rows_total == 2
    assert paper.recomputed_total == Decimal("300.00")
    assert paper.status is Status.PASS


def test_the_formula_is_rendered_in_the_headers_the_preparer_wrote() -> None:
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )

    paper = recalculate(ctx, spec())

    assert paper.basis.formula == "fte_amount + non_fte_amount"
    assert paper.basis.formula_in_headers == "fte_accrual + non_fte_accrual"
    assert paper.basis.stated_column == "D"
    assert paper.basis.currency == "EUR"
    assert paper.basis.rounding == "2 decimal places, half up"


# ------------------------------------------------------------------- it does not reconcile


def test_an_entry_outside_tolerance_fails_and_says_by_how_much() -> None:
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("90.00")]],
    )

    paper = recalculate(ctx, spec())

    assert paper.status is Status.FAIL
    assert paper.difference == Decimal("10.00")
    assert paper.within_tolerance is False
    assert paper.difference_pct == pytest.approx(11.111, rel=1e-3)
    assert "10.00 more than was posted" in paper.explanation
    assert "outside the tolerance" in paper.explanation


def test_a_difference_inside_the_agreed_percentage_is_not_a_failure() -> None:
    """A tenth of a percent of a million is a thousand, and the business said so."""
    ctx = workbook(
        [["E1", Decimal("1000000.00"), Decimal("0.00"), Decimal("1000000.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("999500.00")]],
    )

    paper = recalculate(ctx, spec())

    assert paper.within_tolerance is True
    assert paper.basis.tolerance_amount == Decimal("999.50")
    assert paper.status is not Status.FAIL


def test_a_record_that_differs_from_its_own_stated_amount_is_cited() -> None:
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
            ["E2", Decimal("150.00"), Decimal("50.00"), Decimal("175.00")],
        ]
    )

    paper = recalculate(ctx, spec())

    assert paper.citation_count == 1
    citation = paper.citations[0]
    assert citation.kind is DiscrepancyKind.STATED_DIFFERS
    assert citation.row == 3
    assert citation.stated_cell == "D3"
    assert citation.recomputed == Decimal("200.00")
    assert citation.stated == Decimal("175.00")
    assert citation.difference == Decimal("25.00")
    assert "25.00 more" in citation.why
    assert "200.00" not in citation.why, "the sentence quotes the difference, not the total"
    assert {value.id: value.cell for value in citation.inputs} == {
        "fte_amount": "B3",
        "non_fte_amount": "C3",
    }
    assert paper.row_impact == Decimal("25.00")
    assert paper.rows_agreeing == 1


def test_both_figures_in_a_citation_are_written_at_the_declared_scale() -> None:
    """A cell written at one decimal place must not appear beside a table showing two."""
    ctx = workbook(
        [["E1", Decimal("12556.5"), Decimal("0"), Decimal("12406.5")]],
        posted=[["FR01", "720008", "EUR", Decimal("12556.50")]],
    )

    paper = recalculate(ctx, spec())

    assert paper.citations[0].why.startswith("The approved formula gives 150.00 more than the ")
    assert "12,406.50 this row states" in paper.citations[0].why


def test_an_entry_that_ties_while_a_record_does_not_is_partial_never_a_pass() -> None:
    """Two records compensating for each other is the case a total alone cannot see."""
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("75.00")],
            ["E2", Decimal("150.00"), Decimal("50.00"), Decimal("225.00")],
        ]
    )

    paper = recalculate(ctx, spec())

    assert paper.difference == Decimal("0.00")
    assert paper.status is Status.PARTIAL
    assert paper.ties is True
    assert paper.citation_count == 2


# --------------------------------------------------------------------------- missing inputs


def test_a_record_an_input_is_missing_on_is_cited_and_never_summed_as_zero() -> None:
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
            ["E2", None, Decimal("50.00"), Decimal("200.00")],
        ],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )

    paper = recalculate(ctx, spec())

    assert paper.rows_total == 2
    assert paper.rows_recomputed == 1
    assert paper.rows_incomplete == 1
    assert paper.recomputed_total == Decimal("100.00")
    assert paper.complete is False
    assert paper.status is Status.PARTIAL, "an incomplete recalculation can never be a pass"
    citation = next(c for c in paper.citations if c.kind is DiscrepancyKind.INPUT_MISSING)
    assert citation.row == 3
    assert citation.missing == ("fte_amount",)
    assert citation.recomputed is None
    assert "could not be rebuilt" in citation.why
    assert any("not recomputed" in note for note in paper.notes)


def test_a_required_input_the_workbook_does_not_carry_is_inconclusive() -> None:
    """Never a failure: nothing was established, and that is a different thing."""
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )
    needs_a_rate = spec(
        formula="fte_amount + non_fte_amount + rate",
        inputs=(
            RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
            RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
            RecalcInputSpec(id="rate", meaning="daily_salary_rate", required=True),
        ),
    )

    paper = recalculate(ctx, needs_a_rate)

    assert paper.status is Status.UNABLE_TO_VALIDATE
    assert paper.recomputed_total == Decimal(0)
    assert paper.within_tolerance is None
    assert paper.rows_total == 0
    assert "rate" in paper.explanation
    assert next(item for item in paper.inputs if item.id == "rate").bound is False


def test_an_optional_input_the_workbook_does_not_carry_is_a_note_not_a_gap() -> None:
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )
    with_optional = spec(
        inputs=(
            RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
            RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
            RecalcInputSpec(id="cp", meaning="cp_provision", required=False),
        ),
    )

    paper = recalculate(ctx, with_optional)

    assert paper.status is Status.PASS
    assert any("cp is not present" in note for note in paper.notes)


def test_the_records_each_input_resolved_on_are_counted() -> None:
    ctx = workbook(
        [
            ["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")],
            ["E2", Decimal("150.00"), None, Decimal("150.00")],
        ],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )

    paper = recalculate(ctx, spec())

    counts = {item.id: (item.rows_present, item.rows_missing) for item in paper.inputs}
    assert counts == {"fte_amount": (2, 0), "non_fte_amount": (1, 1)}


# --------------------------------------------------------------------------- the booked side


def test_only_the_debit_side_of_the_posted_entry_is_summed() -> None:
    """The liability credit is the same money again with the other sign."""
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[
            ["FR01", "720008", "EUR", Decimal("100.00")],
            ["FR01", "322000", "EUR", Decimal("-100.00")],
        ],
    )

    paper = recalculate(ctx, spec())

    assert paper.booked.total == Decimal("100.00")
    assert paper.booked.lines == 1
    assert paper.booked.accounts == ("720008",)
    assert paper.booked.method is BookedMethod.POSTING_DEBITS
    assert paper.status is Status.PASS


def test_a_booked_amount_that_cannot_be_read_is_inconclusive_not_a_pass() -> None:
    ctx = make_context(
        {"calculation": (CALCULATION, [["E1", Decimal("1.00"), Decimal("0.00"), Decimal("1.00")]])}
    )

    paper = recalculate(ctx, spec())

    assert paper.booked.total is None
    assert paper.within_tolerance is None
    assert paper.status is Status.UNABLE_TO_VALIDATE
    assert paper.recomputed_total == Decimal("1.00"), "the recomputation still stands on its own"
    assert any("could not be read" in note for note in paper.notes)


def test_a_control_total_the_entry_states_can_be_the_booked_side() -> None:
    ctx = make_context(
        {
            "calculation": (
                CALCULATION,
                [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
            )
        },
        facts={"je_total": Decimal("100.00")},
    )

    paper = recalculate(
        ctx, spec(booked=BookedSpec(method=BookedMethod.STATED_FACT, fact="je_total"))
    )

    assert paper.booked.total == Decimal("100.00")
    assert paper.status is Status.PASS
    assert "states about itself" in paper.booked.description


# --------------------------------------------------------------------------- derived terms


def test_a_derived_term_is_re_performed_and_corroborated_row_by_row() -> None:
    headers = (
        "employee_ref",
        "fte_accrual",
        "non_fte_accrual",
        "total_accrual",
        "rtt_balance_days",
        "daily_salary_rate",
        "rtt_accrual",
    )
    ctx = make_context(
        {
            "calculation": (
                headers,
                [
                    [
                        "E1",
                        Decimal("100.00"),
                        Decimal("0.00"),
                        Decimal("100.00"),
                        Decimal("2"),
                        Decimal("50.00"),
                        Decimal("100.00"),
                    ],
                    [
                        "E2",
                        Decimal("60.00"),
                        Decimal("0.00"),
                        Decimal("60.00"),
                        Decimal("-3"),
                        Decimal("20.00"),
                        Decimal("0.00"),
                    ],
                ],
            ),
            "je_posting": (POSTING, [["FR01", "720008", "EUR", Decimal("160.00")]]),
        }
    )
    with_term = spec(
        inputs=(
            RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
            RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
            RecalcInputSpec(id="days", meaning="rtt_balance_days", required=False),
            RecalcInputSpec(id="rate", meaning="daily_salary_rate", required=False),
        ),
        derive=(
            DerivedTermSpec(
                id="rtt_amount", expression="max(days, 0) * rate", agrees_with="rtt_accrual"
            ),
        ),
    )

    paper = recalculate(ctx, with_term)

    assert paper.status is Status.PASS
    component = paper.components[0]
    assert component.total == Decimal("100.00"), "the floor at zero is the business's rule"
    assert component.rows_populated == 2
    assert component.in_headers == "max(rtt_balance_days, 0) * daily_salary_rate"
    corroboration = paper.corroborations[0]
    assert corroboration.rows_compared == 2
    assert corroboration.rows_agreeing == 2
    assert corroboration.agrees is True
    assert corroboration.label == "rtt_accrual"


def test_a_derived_term_that_disagrees_reports_where_and_by_how_much() -> None:
    headers = (
        "employee_ref",
        "fte_accrual",
        "non_fte_accrual",
        "total_accrual",
        "rtt_balance_days",
        "daily_salary_rate",
        "rtt_accrual",
    )
    ctx = make_context(
        {
            "calculation": (
                headers,
                [
                    [
                        "E1",
                        Decimal("100.00"),
                        Decimal("0.00"),
                        Decimal("100.00"),
                        Decimal("2"),
                        Decimal("50.00"),
                        Decimal("90.00"),
                    ],
                ],
            ),
            "je_posting": (POSTING, [["FR01", "720008", "EUR", Decimal("100.00")]]),
        }
    )
    with_term = spec(
        inputs=(
            RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
            RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
            RecalcInputSpec(id="days", meaning="rtt_balance_days", required=False),
            RecalcInputSpec(id="rate", meaning="daily_salary_rate", required=False),
        ),
        derive=(
            DerivedTermSpec(
                id="rtt_amount", expression="max(days, 0) * rate", agrees_with="rtt_accrual"
            ),
        ),
    )

    paper = recalculate(ctx, with_term)

    corroboration = paper.corroborations[0]
    assert corroboration.agrees is False
    assert corroboration.rows_agreeing == 0
    assert corroboration.impact == Decimal("10.00")
    assert corroboration.first_example == "row 2"
    assert paper.status is Status.PASS, "a leg disagreeing does not move the entry's own tie-out"


# --------------------------------------------------------------------------- the refusals


def test_a_working_the_map_never_bound_raises_for_the_caller_to_report() -> None:
    ctx = make_context({"je_posting": (POSTING, [["FR01", "720008", "EUR", Decimal("1.00")]])})

    with pytest.raises(ToolError, match="calculation"):
        recalculate(ctx, spec())


def test_the_provisional_basis_is_said_on_the_face_of_the_paper() -> None:
    ctx = workbook(
        [["E1", Decimal("100.00"), Decimal("0.00"), Decimal("100.00")]],
        posted=[["FR01", "720008", "EUR", Decimal("100.00")]],
    )

    paper = recalculate(ctx, spec(provisional_basis=True))

    assert paper.basis.provisional_basis is True
    assert any("provisional" in note for note in paper.notes)


# ------------------------------------------------------- the failing review stays cheap


def test_a_workbook_where_nothing_reconciles_does_not_collect_a_citation_per_row() -> None:
    """The bound that matters when something has already gone wrong.

    A formula that does not hold for a file produces a discrepancy on every record. The
    counts stay exact because they are counters; the citations are pooled worst-first per
    kind, so a 500-record failure does not build 500 models to print twelve of them.
    """
    records: Records = [
        [f"E{index}", Decimal(index), Decimal(0), Decimal(index + 1000)] for index in range(1, 501)
    ]
    ctx = workbook(records, posted=[["FR01", "720008", "EUR", Decimal("125250.00")]])

    paper = recalculate(ctx, spec())

    assert paper.rows_total == 500
    assert paper.citation_count == 500, "the count is exact"
    assert paper.row_impact == Decimal(500 * 1000), "and so is the money"
    assert len(paper.citations) <= CITATION_CAP
    # Worst first, and they are the actual worst: every row differs by the same 1,000 here,
    # so the ordering is stable rather than arbitrary.
    assert all(citation.kind is DiscrepancyKind.STATED_DIFFERS for citation in paper.citations)


def test_one_citation_of_every_kind_survives_the_cap() -> None:
    """A missing input has no difference to rank by, so money alone would hide every one."""
    records: Records = [
        [f"E{index}", Decimal(index), Decimal(0), Decimal(index + 1000)] for index in range(1, 40)
    ]
    records.append(["E99", None, Decimal(0), Decimal(5)])
    ctx = workbook(records, posted=[["FR01", "720008", "EUR", Decimal("780.00")]])

    paper = recalculate(ctx, spec())

    kinds = {citation.kind for citation in paper.citations}
    assert DiscrepancyKind.STATED_DIFFERS in kinds
    assert DiscrepancyKind.INPUT_MISSING in kinds
    assert len(paper.citations) <= CITATION_CAP
