"""Why one record does not reconcile, worked out for the person who has to fix it.

A work paper that says "47,075.01 more than this row states" is complete and correct, and
still leaves a reviewer with 2,273 rows and forty columns to search. On every file seen so
far the whole entry-level difference comes from a single cell, and finding it is mechanical
work the harness can do: re-run the approved formula over the record's own inputs, one
input at a time, and ask what that input would have to be for the record's own stated
amount to come out.

Three rules hold this honest.

**It solves; it never guesses.** The expected value is found by evaluating the business's
own formula, and accepted only when re-evaluating at the answer reproduces the stated
amount inside the row tolerance. An input that cannot be solved is not reported.

**One explanation, or none.** Where two inputs each reconcile the record the answer is
ambiguous and nothing is reported - a confident sentence naming the wrong cell is worse
than no sentence at all. The figures and the citation are on the report either way.

**It asks rather than concludes.** The record agreeing at some value is evidence about
where to look. The preparer may have typed the wrong rate, or the formula may not be the
one this file follows. So the wording is "it should be about", and the next step is to ask
the preparer - never "the preparer entered 100,000 by mistake".

The worked example beside it is the same arithmetic run twice, under the business's own
step list (``walkthrough`` on the JE type): once with the file's value and once with the
solved one, so a reader sees exactly where the two part company.
"""

import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from review.models.context import RecalculationSpec, WalkthroughStepSpec
from review.models.recalculation import (
    RecalcCitation,
    RecalcUnit,
    RowDiagnosis,
    RowExampleStep,
    RowFault,
)
from review.recalc.formula import FormulaError, compile_formula

logger = logging.getLogger(__name__)

_PROBE_LOW = Decimal("0")
_PROBE_HIGH = Decimal("1")

#: How far the solved value must stand out from the one the file carries before the
#: record is reported as explained by it. A rate that is out by a twentieth is rounding
#: or a stale figure; one that is out by a factor of hundreds is a different quantity in
#: the cell. Below this the report says nothing, because "about the same" does not
#: identify a cell to anybody.
_MIN_DEVIATION = Decimal("0.25")


def diagnose_row(
    spec: RecalculationSpec,
    citation: RecalcCitation,
    *,
    entry_difference: Decimal | None = None,
) -> RowDiagnosis | None:
    """Trace one record's difference to a single input, where exactly one explains it."""
    stated, recomputed = citation.stated, citation.recomputed
    if citation.kind != "stated_differs" or stated is None or recomputed is None:
        return None

    values = _row_values(spec, citation)
    if values is None:
        return None

    candidates = [
        solved
        for item in spec.inputs
        if (solved := _solve(spec, values, item.id, stated)) is not None
    ]
    explaining = [found for found in candidates if found.deviation >= _MIN_DEVIATION]
    if len(explaining) != 1:
        # None, or several - either way no single cell is the answer.
        logger.info(
            "row %s: %d input(s) reconcile the record, so none is reported",
            citation.row,
            len(explaining),
        )
        return None

    found = explaining[0]
    fault = _fault(spec, citation, found)
    if fault is None:
        return None

    corrected = dict(values) | {found.input_id: found.expected}
    difference = recomputed - stated

    return RowDiagnosis(
        row=citation.row,
        sheet=citation.sheet,
        fault=fault,
        where=_where(citation, fault),
        problem=(
            f"The {_plain(fault.label)} is {_number(fault.value)}. "
            f"It should be about {_number(fault.expected)}."
        ),
        impact=_impact(stated, recomputed, difference, entry_difference),
        next_step=(
            f"Ask the preparer for the right {_plain(fault.label)} and fix cell {fault.cell}."
        ),
        steps=_steps(spec, values, corrected),
        stated=stated,
        recomputed=recomputed,
        difference=difference,
    )


class _Solved:
    """One input, and the value that would make this record agree."""

    __slots__ = ("deviation", "expected", "input_id")

    def __init__(self, input_id: str, expected: Decimal, deviation: Decimal) -> None:
        self.input_id = input_id
        self.expected = expected
        self.deviation = deviation


def _row_values(spec: RecalculationSpec, citation: RecalcCitation) -> dict[str, Decimal] | None:
    """The record's inputs by id, as the formula reads them. ``None`` if any is missing."""
    values: dict[str, Decimal] = {}
    for reading in citation.inputs:
        if reading.value is None:
            return None
        values[reading.id] = reading.value
    # Constants the business states rather than reads off the file - a day-count
    # denominator, a statutory rate - are not cited on a row but the formula needs them.
    for declared in spec.inputs:
        if declared.value is not None:
            values.setdefault(declared.id, declared.value)
    return values if values else None


def _evaluate(spec: RecalculationSpec, values: Mapping[str, Decimal]) -> Decimal | None:
    """The approved formula on one record, derived terms first, exactly as the engine runs it."""
    row: dict[str, Decimal | None] = dict(values)
    try:
        for term in spec.derive:
            row[term.id] = compile_formula(term.expression, spec.names).evaluate(row)
        return compile_formula(spec.formula, spec.names).evaluate(row)
    except (FormulaError, InvalidOperation, ArithmeticError):  # pragma: no cover - defensive
        logger.warning("row diagnosis could not evaluate the formula", exc_info=True)
        return None


def _solve(
    spec: RecalculationSpec,
    values: Mapping[str, Decimal],
    input_id: str,
    stated: Decimal,
) -> _Solved | None:
    """What ``input_id`` would have to be for this record to come out at ``stated``.

    Two probes and a straight line, then a third evaluation to check the answer. Every
    formula in the catalogue is linear in each single input once the others are fixed; the
    check is what makes that an observation rather than an assumption, and a conditional
    that steps between the probes simply fails it and reports nothing.
    """
    if input_id not in values:
        return None

    low = _evaluate(spec, dict(values) | {input_id: _PROBE_LOW})
    high = _evaluate(spec, dict(values) | {input_id: _PROBE_HIGH})
    if low is None or high is None:
        return None

    slope = high - low
    if slope == 0:
        return None

    expected = (stated - low) / slope
    if not expected.is_finite():  # pragma: no cover - guarded by the slope test above
        return None

    check = _evaluate(spec, dict(values) | {input_id: expected})
    if check is None or abs(check - stated) > spec.row_tolerance:
        return None

    actual = values[input_id]
    if expected < 0 <= actual:
        # A negative rate or a negative day count is not an explanation of anything.
        return None

    scale = max(abs(expected), abs(actual), Decimal(1))
    return _Solved(input_id, _tidy(expected), abs(actual - expected) / scale)


def _fault(spec: RecalculationSpec, citation: RecalcCitation, found: _Solved) -> RowFault | None:
    """The solved input as a cell a reader can open."""
    reading = next((item for item in citation.inputs if item.id == found.input_id), None)
    if reading is None:  # pragma: no cover - solved ids come from the citation's own inputs
        return None

    declared = next((item for item in spec.inputs if item.id == found.input_id), None)
    return RowFault(
        input_id=found.input_id,
        label=_single_line(reading.label),
        cell=reading.cell,
        column="".join(character for character in reading.cell if character.isalpha()),
        value=reading.value,
        expected=found.expected,
        unit=declared.unit if declared else RecalcUnit.CURRENCY,
    )


def _where(citation: RecalcCitation, fault: RowFault) -> str:
    sheet = f"{citation.sheet} sheet, " if citation.sheet else ""
    column = f", column {fault.column} ({_plain(fault.label)})" if fault.column else ""
    return f"{sheet}row {citation.row}{column}."


def _impact(
    stated: Decimal,
    recomputed: Decimal,
    difference: Decimal,
    entry_difference: Decimal | None,
) -> str:
    """What the record does, and whether it is the whole of the entry's difference.

    The second sentence is only written where the two figures actually match: a record
    that carries most of a difference is not the same finding as one that carries all of
    it, and the first reads as the second to anybody skimming.
    """
    direction = "extra" if difference > 0 else "less"
    sentence = (
        f"This employee's amount becomes {_number(recomputed)} instead of {_number(stated)}. "
        f"That is {_number(abs(difference))} {direction}"
    )
    if entry_difference is not None and abs(abs(entry_difference) - abs(difference)) <= Decimal(1):
        return f"{sentence} - the full difference in the total."
    return f"{sentence}."


def _steps(
    spec: RecalculationSpec,
    values: Mapping[str, Decimal],
    corrected: Mapping[str, Decimal],
) -> tuple[RowExampleStep, ...]:
    """The business's own step list, carried out on this record both ways."""
    walkthrough = spec.walkthrough
    if walkthrough is None:
        return ()

    correct_scope = _scope(spec, corrected)
    actual_scope = _scope(spec, values)

    steps: list[RowExampleStep] = []
    for step in walkthrough.steps:
        if not step.expr:
            continue
        correct = _step_value(spec, step, correct_scope)
        actual = _step_value(spec, step, actual_scope)
        if correct is None or actual is None:
            continue
        correct_scope[step.id] = correct
        actual_scope[step.id] = actual

        differs = correct != actual
        steps.append(
            RowExampleStep(
                label=step.label or step.id,
                correct=_written(step, correct_scope, correct),
                # Unchanged steps print the number alone: repeating the arithmetic in both
                # columns invites a reader to hunt for a difference that is not there.
                actual=_written(step, actual_scope, actual) if differs else _number(actual),
                differs=differs,
            )
        )
    return tuple(steps)


def _scope(spec: RecalculationSpec, values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """The record's inputs plus every derived term, for a step to draw on."""
    scope = dict(values)
    row: dict[str, Decimal | None] = dict(values)
    for term in spec.derive:
        try:
            computed = compile_formula(term.expression, spec.names).evaluate(row)
        except (FormulaError, InvalidOperation, ArithmeticError):  # pragma: no cover
            continue
        row[term.id] = computed
        if computed is not None:
            scope[term.id] = computed
    return scope


def _step_value(
    spec: RecalculationSpec, step: WalkthroughStepSpec, scope: Mapping[str, Decimal]
) -> Decimal | None:
    try:
        row: dict[str, Decimal | None] = dict(scope)
        return compile_formula(step.expr, frozenset()).evaluate(row)
    except (FormulaError, InvalidOperation, ArithmeticError):  # pragma: no cover - authored
        logger.warning("walkthrough step %s does not evaluate", step.id, exc_info=True)
        return None


def _written(step: WalkthroughStepSpec, scope: Mapping[str, Decimal], result: Decimal) -> str:
    """The step's own arithmetic with this record's numbers in it, and its answer."""
    if not step.shows:
        return _number(result)

    written = step.shows
    for name, value in scope.items():
        written = written.replace(f"{{{name}}}", _number(value))

    answer = _number(result)
    return answer if written.strip() == answer else f"{written} = {answer}"


def _tidy(value: Decimal) -> Decimal:
    """A solved value at the precision a reader can act on, never more."""
    quantum = Decimal("0.01")
    try:
        return value.quantize(quantum)
    except InvalidOperation:  # pragma: no cover - only for absurd magnitudes
        return value


def _number(value: Decimal | None) -> str:
    """Grouped, and carrying decimals only where they say something.

    A day count of 10 reads as "10" and a rate of 311.73 as "311.73": printing either at
    the other's precision is how a worked example starts looking like a different sum.
    """
    if value is None:
        return "-"
    if value == value.to_integral_value():
        return f"{value.to_integral_value():,}"
    return f"{value:,.2f}"


def _single_line(text: str) -> str:
    """A header as one line. Workbook headers wrap, and the newline comes with them."""
    return " ".join(text.split())


def _plain(label: str) -> str:
    """A column header as it reads mid-sentence: "Daily Salary (RTT Rate)" -> "daily salary"."""
    cleaned = _single_line(label)
    bracket = cleaned.find("(")
    if bracket > 0:
        cleaned = cleaned[:bracket].strip()
    return cleaned.lower()
