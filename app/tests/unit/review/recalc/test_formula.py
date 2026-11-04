"""The formula parser: what it accepts, what it refuses, and how it treats a missing input.

The refusals matter as much as the arithmetic. This expression is read out of a file a
business owner edits, so every one of these failures has to happen at startup naming the
problem rather than halfway through a review, and nothing in the grammar may reach Python.
"""

from decimal import Decimal

import pytest

from review.recalc.formula import FormulaError, compile_formula, expand, substitute


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
        ("coalesce(a, 0)", Decimal(10)),
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


def test_coalesce_is_the_one_deliberate_exception() -> None:
    """Every other function in the grammar turns a missing input into no amount at all.

    ``coalesce`` exists because a blank cell is sometimes the business's own answer - one leg
    of a two-leg provision that does not apply this period - rather than a gap, and that has
    to be written explicitly in the business's own expression rather than assumed by the
    engine for every formula.
    """
    assert evaluate("coalesce(a, 0)", a=None) == Decimal(0)
    assert evaluate("coalesce(a, 0)", a=Decimal(5)) == Decimal(5)
    # The default side still propagates like anything else: a fallback that is itself
    # missing is still missing, not silently zero.
    assert evaluate("coalesce(a, b)", a=None, b=None) is None


# ------------------------------------------------------------- conditions and branches


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("a == b", Decimal(0)),
        ("a != b", Decimal(1)),
        ("a < b", Decimal(1)),
        ("a <= b", Decimal(1)),
        ("a > b", Decimal(0)),
        ("a >= b", Decimal(0)),
    ],
)
def test_a_comparison_answers_one_or_zero(expression: str, expected: Decimal) -> None:
    """Answering a number rather than a bool keeps a condition composable with the rest."""
    assert evaluate(expression, a=Decimal(10), b=Decimal(20)) == expected


def test_a_comparison_binds_looser_than_arithmetic() -> None:
    assert evaluate("a + b == 30", a=Decimal(10), b=Decimal(20)) == Decimal(1)


def test_a_comparison_with_an_absent_side_has_no_answer() -> None:
    """Not false. An absent value answers neither yes nor no, and reading it as a failed
    test is how one missing column becomes a confident finding about every record."""
    assert evaluate("a == b", a=None, b=Decimal(1)) is None
    assert evaluate("a > b", a=Decimal(1), b=None) is None


def test_the_conditional_takes_exactly_one_branch() -> None:
    assert evaluate("if(a == 0, 100, 200)", a=Decimal(0)) == Decimal(100)
    assert evaluate("if(a == 0, 100, 200)", a=Decimal(1)) == Decimal(200)


def test_the_conditional_does_not_evaluate_the_branch_it_does_not_take() -> None:
    """The point of the laziness. The branch not taken may read an input this row does not
    carry, and evaluating it anyway would make the row incomplete over a figure the business
    never asked for on it."""
    assert evaluate("if(a == 0, 0, b / a)", a=Decimal(0), b=None) == Decimal(0)
    assert evaluate("if(a == 0, b, 0)", a=Decimal(1), b=None) == Decimal(0)


def test_a_condition_with_no_answer_produces_no_amount() -> None:
    assert evaluate("if(a == 0, 1, 2)", a=None) is None


@pytest.mark.parametrize(
    ("taken", "expected"),
    [
        (None, Decimal(0)),
        (Decimal(1), Decimal("5.6667")),
        # The case that rules out the arithmetic stand-in. Scaling the leg by
        # min(taken, 1) halves a half day; three records across the two real workbooks do
        # exactly this, which is why the grammar grew a conditional rather than a trick.
        (Decimal("0.5"), Decimal("6.1667")),
        # Taken more than earned: floored, never negative.
        (Decimal(9), Decimal(0)),
    ],
)
def test_the_leave_convention_the_conditional_exists_for(
    taken: Decimal | None, expected: Decimal
) -> None:
    """An employee who has taken no RTT accrues none of it, which nothing in the four
    operators reproduces. Written as the business states it, over a blank that means none
    were taken rather than that the figure is unknown."""
    assert (
        evaluate(
            "if(coalesce(taken, 0) == 0, 0, max(earned - taken, 0))",
            earned=Decimal("6.6667"),
            taken=taken,
        )
        == expected
    )


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
        "coalesce(a)",
        "coalesce(a, b, c)",
        "if(a, b)",
        "if(a, b, c, d)",
        # A chained comparison has two defensible readings, so it is refused rather than
        # guessed at.
        "a < b < c",
        # Excel's single `=` and a mistyped operator are both typos, not dialects.
        "a = b",
        "a === b",
        "a =< b",
        "a <> b",
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


# ---------------------------------------------------- writing the stages out in full


STAGES = [
    ("earned", "coalesce(entitlement, 0) / 12 * months"),
    ("unused", "max(earned - taken, 0)"),
    ("amount", "unused * rate"),
]
HEADERS = {
    "entitlement": "Acquis RTT",
    "months": "Months to Accrue",
    "taken": "Pris RTT",
    "rate": "Daily Salary (RTT Rate)",
    "provision": "Total Provision CP",
}


def test_expansion_writes_every_stage_out_in_the_preparers_columns() -> None:
    """The point of it: a stage has no column, so a formula naming one hides the columns
    behind it. After expansion every name on the page is a header off this workbook."""
    written = expand("provision + amount / 12", STAGES, HEADERS)

    assert written == (
        "Total Provision CP + "
        "((max((coalesce(Acquis RTT, 0) / 12 * Months to Accrue) - Pris RTT, 0)) "
        "* Daily Salary (RTT Rate)) / 12"
    )
    for name, _ in STAGES:
        assert name not in written
    for declared in ("entitlement", "months", "taken", "rate", "provision"):
        assert declared not in written


def test_each_stage_is_parenthesised_as_it_goes_in() -> None:
    """Inlining a sum unparenthesised would silently change the arithmetic: ``x + a + b / 12``
    is not ``x + (a + b) / 12``, and a displayed formula that is not the evaluated one is
    worse than one nobody can read."""
    assert expand("x + s / 12", [("s", "a + b")], {}) == "x + (a + b) / 12"


def test_a_stage_may_read_an_earlier_stage() -> None:
    """Declaration order is evaluation order, which is what makes one pass enough."""
    assert (
        expand("c", [("a", "1 + 1"), ("b", "a * 2"), ("c", "b + 3")], {}) == "(((1 + 1) * 2) + 3)"
    )


def test_expansion_leaves_a_name_it_has_no_header_for() -> None:
    """A workbook that binds nothing still shows the formula, in the ids the business wrote,
    rather than a blank where the arithmetic should be."""
    assert expand("provision + amount", [("amount", "unused * rate")], {}) == (
        "provision + (unused * rate)"
    )


def test_expansion_never_renames_a_function() -> None:
    assert expand("max(a, b)", [], {"max": "NOPE", "a": "A", "b": "B"}) == "max(A, B)"


def test_expansion_with_no_stages_is_a_plain_substitution() -> None:
    assert expand("provision + 1", [], HEADERS) == substitute("provision + 1", HEADERS)
