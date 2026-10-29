"""The approved basis as the loader validates it.

This block is edited by a business owner, so every one of these has to fail at startup
naming the problem. A formula with a typo in it that loads and then evaluates to nothing on
every row would report a reconciling entry as inconclusive, which is the worst of the
failure modes available here: it looks like a finding about the workbook.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from review.models.context import (
    BookedSpec,
    DerivedTermSpec,
    JETypeContext,
    RecalcInputSpec,
    RecalculationSpec,
    RoundingSpec,
)
from review.models.recalculation import BookedMethod, RecalcUnit, RoundingMode


def spec(**overrides: object) -> RecalculationSpec:
    base: dict[str, object] = {
        "formula": "a + b",
        "inputs": (
            RecalcInputSpec(id="a", meaning="fte_accrual"),
            RecalcInputSpec(id="b", meaning="non_fte_accrual"),
        ),
    }
    base.update(overrides)
    return RecalculationSpec.model_validate(base)


def test_the_defaults_are_the_conventions_the_design_states() -> None:
    basis = spec()

    assert basis.tolerance_pct == Decimal("0.1")
    assert basis.row_tolerance == Decimal("0.01")
    assert basis.rounding == RoundingSpec(decimals=2, mode=RoundingMode.HALF_UP)
    assert basis.rounding.describe() == "2 decimal places, half up"
    assert basis.booked.method is BookedMethod.POSTING_DEBITS
    assert basis.provisional_basis is False


def test_a_formula_naming_something_undeclared_is_a_startup_error() -> None:
    with pytest.raises(ValidationError, match="not a declared input"):
        spec(formula="a + c")


def test_a_malformed_formula_is_a_startup_error() -> None:
    with pytest.raises(ValidationError, match="formula"):
        spec(formula="a + ")


def test_a_derived_term_may_read_the_inputs_and_the_terms_before_it() -> None:
    basis = spec(
        formula="first + second",
        derive=(
            DerivedTermSpec(id="first", expression="a * 2"),
            DerivedTermSpec(id="second", expression="first + b"),
        ),
    )

    assert [term.id for term in basis.derive] == ["first", "second"]
    assert basis.names == {"a", "b", "first", "second"}


def test_a_derived_term_cannot_read_one_declared_after_it() -> None:
    """Declaration order is evaluation order, which is what makes a cycle unwritable."""
    with pytest.raises(ValidationError, match="not a declared input"):
        spec(
            formula="first",
            derive=(
                DerivedTermSpec(id="first", expression="second + 1"),
                DerivedTermSpec(id="second", expression="a"),
            ),
        )


def test_an_id_declared_twice_is_a_startup_error() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        spec(
            inputs=(
                RecalcInputSpec(id="a", meaning="fte_accrual"),
                RecalcInputSpec(id="a", meaning="non_fte_accrual"),
            )
        )


def test_an_input_must_be_a_column_or_a_constant_and_not_both() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        RecalcInputSpec(id="a", meaning="fte_accrual", value=Decimal(30))


def test_an_input_that_is_neither_a_column_nor_a_constant_is_rejected() -> None:
    """A name with nothing behind it would evaluate to nothing on every row, silently."""
    with pytest.raises(ValidationError, match="exactly one"):
        RecalcInputSpec(id="a")


def test_a_business_constant_carries_its_own_unit() -> None:
    """The day-count denominator case: declared, with an owner, never a 30 in an expression."""
    denominator = RecalcInputSpec(
        id="working_days", value=Decimal("21.67"), unit=RecalcUnit.DAYS, required=True
    )

    assert denominator.meaning is None
    assert spec(
        formula="a / working_days",
        inputs=(RecalcInputSpec(id="a", meaning="fte_accrual"), denominator),
    ).names == {"a", "working_days"}


def test_a_typo_in_the_block_name_is_a_startup_error() -> None:
    """``extra="forbid"`` is why a misspelled key is an error and not a rule doing nothing."""
    with pytest.raises(ValidationError):
        RecalculationSpec.model_validate(
            {
                "formula": "a",
                "inputs": [{"id": "a", "meaning": "fte_accrual"}],
                "tolerence_pct": 5,
            }
        )


def test_an_entry_type_without_a_basis_is_allowed() -> None:
    """Most JE types will have no approved formula yet, and that is reported, not guessed."""
    assert JETypeContext.model_fields["recalculation"].default is None


def test_the_booked_side_is_a_table_and_a_column_not_a_cell() -> None:
    booked = BookedSpec(table="je_staging", amount="line_amount")

    assert booked.method is BookedMethod.POSTING_DEBITS
    assert booked.table == "je_staging"
    assert booked.amount == "line_amount"


def test_the_booked_side_can_only_be_read_from_outside_the_working() -> None:
    """Summing a column of the working would double-count its subtotal rows, and would take
    the booked figure from the table the recomputation itself walks."""
    assert set(BookedMethod) == {BookedMethod.POSTING_DEBITS, BookedMethod.STATED_FACT}
