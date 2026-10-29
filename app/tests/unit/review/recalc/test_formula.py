"""The formula parser: what it accepts, what it refuses, and how it treats a missing input.

The refusals matter as much as the arithmetic. This expression is read out of a file a
business owner edits, so every one of these failures has to happen at startup naming the
problem rather than halfway through a review, and nothing in the grammar may reach Python.
"""

from decimal import Decimal

import pytest

from review.recalc.formula import FormulaError, compile_formula, substitute


def evaluate(expression: str, **values: Decimal | None) -> Decimal | None:
    return compile_formula(expression).evaluate(values)


# ------------------------------------------------------------------------- arithmetic


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("a + b", Decimal(30)),
        ("a - b", Decimal(-10)),
        ("a * b", Decimal(200)),
        ("b / a", Decimal(2)),
        ("a + b * 2", Decimal(50)),
        ("(a + b) * 2", Decimal(60)),
        ("-a + b", Decimal(10)),
        ("max(a, b)", Decimal(20)),
        ("min(a, b)", Decimal(10)),
        ("max(a - b, 0)", Decimal(0)),
        ("abs(a - b)", Decimal(10)),
        ("round(a / 3, 2)", Decimal("3.33")),
        ("a * 1.5", Decimal(15)),
    ],
)
def test_the_grammar_evaluates_as_arithmetic_reads(expression: str, expected: Decimal) -> None:
    assert evaluate(expression, a=Decimal(10), b=Decimal(20)) == expected


def test_precedence_is_not_left_to_right() -> None:
    """The one bug in a hand-written parser that produces plausible wrong numbers."""
    assert evaluate(
        "days * rate + charges", days=Decimal(2), rate=Decimal(100), charges=Decimal(5)
    ) == Decimal(205)
    assert evaluate(
        "charges + days * rate", days=Decimal(2), rate=Decimal(100), charges=Decimal(5)
    ) == Decimal(205)


def test_every_amount_stays_decimal() -> None:
    """Thirds of a cent are why this is Decimal and not float, all the way down."""
    value = evaluate("a / 3", a=Decimal("0.10"))
    assert isinstance(value, Decimal)
    assert str(value).startswith("0.0333333333")


# --------------------------------------------------------------- a missing input propagates


@pytest.mark.parametrize(
    "expression",
    ["a + b", "a * b", "a - b", "a / b", "max(a, b)", "min(a, b)", "abs(b)", "round(b, 2)", "-b"],
)
def test_an_absent_input_produces_no_amount(expression: str) -> None:
    """Never a zero. A row the formula cannot be evaluated on is incomplete, not nought."""
    assert evaluate(expression, a=Decimal(10), b=None) is None


def test_a_zero_denominator_produces_no_amount() -> None:
    """A denominator of nothing is a figure this row does not have, not an exception."""
    assert evaluate("a / b", a=Decimal(10), b=Decimal(0)) is None


def test_an_input_the_row_does_not_carry_at_all_produces_no_amount() -> None:
    assert evaluate("a + missing", a=Decimal(10)) is None


# ------------------------------------------------------------------------- the refusals


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "   ",
        "a +",
        "a + + ",
        "a b",
        "(a + b",
        "a + b)",
        "a ** b",
        "a % b",
        "a; b",
        "sum(a, b)",
        "__import__('os')",
        "a.real",
        "max(a)",
        "round(a)",
        "round(a, 2, 3)",
    ],
)
def test_anything_outside_the_grammar_fails_to_load(expression: str) -> None:
    with pytest.raises(FormulaError):
        compile_formula(expression)


def test_a_name_that_is_not_a_declared_input_fails_to_load() -> None:
    """The typo case. It must be a startup error, not a term that is quietly always absent."""
    with pytest.raises(FormulaError, match="not a declared input"):
        compile_formula("fte_amount + non_fte_amont", frozenset({"fte_amount", "non_fte_amount"}))


def test_the_names_it_reads_are_reported() -> None:
    compiled = compile_formula("max(days, 0) * rate", frozenset({"days", "rate", "unused"}))
    assert compiled.names == frozenset({"days", "rate"})


# ------------------------------------------------------------------------- substitution


def test_substitution_replaces_names_with_the_headers_the_preparer_wrote() -> None:
    assert (
        substitute(
            "max(rtt_days, 0) * daily_rate",
            {"rtt_days": "Balance RTT Days", "daily_rate": "Daily Salary"},
        )
        == "max(Balance RTT Days, 0) * Daily Salary"
    )


def test_substitution_leaves_a_name_it_has_no_header_for() -> None:
    """A half-translated formula is still readable; an invented one is not."""
    assert substitute("a + b", {"a": "Amount"}) == "Amount + b"


def test_substitution_never_renames_a_function() -> None:
    """``max`` is arithmetic, whatever a business file happens to call one of its inputs."""
    assert substitute("max(a, 0)", {"max": "nonsense", "a": "Days"}) == "max(Days, 0)"
