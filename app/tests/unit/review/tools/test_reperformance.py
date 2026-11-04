"""The row-level work that is not a formula: ``RttBalanceReperformTool`` in particular.

The two blanks it folds to zero rather than reads as missing are the point of the file, and
each is pinned here with the business behaviour that justifies it: a blank "Pris RTT" is the
balance staying at zero rather than the earned-to-date amount nothing has yet reduced, and a
blank "Acquis RTT" is an employee with no RTT entitlement this period, not a data gap.
"""

from decimal import Decimal

from review.models.evidence import Figure
from review.models.workbook_data import CellValue
from review.tools.base import run_tool
from review.tools.reperformance import RttBalanceReperformTool
from tests.unit.review.tools.conftest import make_context

COLUMNS = ("employee_ref", "rtt_earned", "rtt_taken", "months_to_accrue", "rtt_balance_days")
Records = list[list[CellValue]]


def run(records: Records) -> list[Figure]:
    ctx = make_context({"calculation": (COLUMNS, records)})
    return run_tool(RttBalanceReperformTool(), ctx, "a_check", table="calculation")


def test_a_balance_that_reproduces_from_earned_taken_and_months_passes() -> None:
    # 10 days earned a year, 8 of 12 months accrued, 1 already taken: (10/12*8) - 1 = 5.6667.
    records: Records = [["E1", Decimal(10), Decimal(1), Decimal(8), Decimal("5.666666666666667")]]

    figure = run(records)[0]

    assert figure.ok is True
    assert figure.coverage is not None
    assert figure.coverage.complete is True
    assert "1 of 1 rows agree" in figure.summary


def test_a_blank_taken_is_not_carried_forward_as_the_full_earned_balance() -> None:
    """The counter-intuitive rule, verified against both sample workbooks: nothing taken means
    the stated balance is zero, not the earned-to-date amount a plain floor would give."""
    records: Records = [["E1", Decimal(10), None, Decimal(8), Decimal(0)]]

    figure = run(records)[0]

    assert figure.ok is True, "the file's own zero is exactly what this reproduces"


def test_a_blank_earned_reproduces_as_no_entitlement_rather_than_unbound() -> None:
    """An employee with no RTT entitlement this period, not a data gap - verified on both
    sample workbooks, never once the exception among the employees who have none."""
    records: Records = [["E1", None, None, Decimal(8), Decimal(0)]]

    figure = run(records)[0]

    assert figure.ok is True
    assert figure.coverage is not None
    assert figure.coverage.rows_bound == 1, "folded to zero, not left unbound"


def test_a_record_that_differs_is_reported_with_its_cell_and_priced() -> None:
    tying: list[CellValue] = [
        "Ex",
        Decimal(10),
        Decimal(1),
        Decimal(8),
        Decimal("5.666666666666667"),
    ]
    records: Records = [
        ["E1", Decimal(10), Decimal(1), Decimal(8), Decimal(2)],
        list(tying),
        list(tying),
        list(tying),
    ]

    figure = run(records)[0]

    assert figure.ok is False
    assert figure.exception_count == 1
    assert figure.samples[0]["cell"] == "E2"
    assert figure.samples[0]["expected"] == Decimal(10) / 12 * Decimal(8) - Decimal(1)
    assert figure.samples[0]["actual"] == Decimal(2)


def test_a_row_missing_months_or_the_stated_balance_is_unbound_not_tied() -> None:
    """Unlike a blank "Acquis RTT" or "Pris RTT", nothing verified reproduces these as zero -
    a genuinely unresolved row stays unresolved."""
    records: Records = [
        ["E1", Decimal(10), Decimal(1), None, Decimal("5.666666666666667")],
        ["E2", Decimal(10), Decimal(1), Decimal(8), None],
    ]

    figure = run(records)[0]

    assert figure.coverage is not None
    assert figure.coverage.rows_bound == 0
    assert figure.exception_count == 2, "both unresolved rows are reported"


def test_an_identity_that_fails_nearly_everywhere_is_unable_to_validate() -> None:
    records: Records = [
        ["E1", Decimal(10), Decimal(1), Decimal(8), Decimal(2)],
        ["E2", Decimal(10), Decimal(1), Decimal(8), Decimal(3)],
        ["E3", Decimal(10), Decimal(1), Decimal(8), Decimal("5.666666666666667")],
    ]

    figure = run(records)[0]

    assert figure.ok is None
    assert figure.error == "rtt_balance_convention_unknown"


def test_a_workbook_missing_the_columns_is_unable_to_validate() -> None:
    ctx = make_context({"calculation": (("employee_ref",), [["E1"]])})

    figure = run_tool(RttBalanceReperformTool(), ctx, "a_check", table="calculation")[0]

    assert figure.ok is None
    assert figure.error == "rtt_balance_columns_missing"
