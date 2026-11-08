"""Step 13c. The entry, rebuilt row by row from the inputs the business approved.

One pass over the working, in Decimal, with no model anywhere near it. For each record the
declared inputs are read, the derived terms are evaluated in declaration order, the approved
formula produces that record's amount, and the amount is compared against what the workbook
states for the same record. The record amounts are summed into the recomputed entry total,
and that total is compared against the booked amount - read from the posted entry's own
lines, which is a different table on a different tab.

Why it is one pass. The widest sample working is 37,613 rows and the deepest formula reads
eight columns, so anything that walks the rows twice walks a quarter of a million cells
twice. The loop below reads each cell once, straight out of the sheet's sparse dict, into a
single reusable row mapping; the formulas are closures compiled once (see ``formula.py``).
On the binary sample that is a fraction of a second rather than a visible pause in
somebody's review.

What it will not do:

- **Never patch a missing input with a zero.** A record an input does not resolve on is
  counted as incomplete and cited. Nothing is summed for it.
- **Never make the booked side agree.** The booked amount is read from the posting table and
  reported exactly as found, including when it cannot be found at all.
- **Never conclude.** The status here is a measurement the routing rules at step 16 use; the
  check's outcome comes from the figures this produces, like every other check.
- **Never name a row.** A citation is a row number, cell references and amounts.
"""

import heapq
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Final

from review.models.context import (
    DerivedTermSpec,
    RecalcExplainedSpec,
    RecalcInputSpec,
    RecalculationSpec,
    ReperformanceSpec,
)
from review.models.recalculation import (
    BookedAmount,
    BookedMethod,
    CitedValue,
    DiscrepancyKind,
    EntryRecalculation,
    FormulaFinding,
    RecalcBasis,
    RecalcCitation,
    RecalcComponent,
    RecalcCorroboration,
    RecalcExplained,
    RecalcExplainedStep,
    RecalcInputReading,
    RecalcRow,
    RoundingMode,
    RowDiagnosis,
    Walkthrough,
    WalkthroughStep,
)
from review.models.verdicts import Status
from review.models.workbook_data import Cell, Sheet
from review.recalc.formula import CompiledFormula, compile_formula, expand, substitute
from review.recalc.formula_audit import audit_formulas
from review.recalc.row_diagnosis import diagnose_row
from review.tools.base import BoundTable, ToolContext, ToolError
from review.utils.sheet_utils import column_index, column_letter

logger = logging.getLogger(__name__)

MAX_REPORTED_ACCOUNTS: Final = 6
"""Accounts named beside the booked total. Enough to see the expense split, not a ledger."""

CITATION_CAP: Final = 12
"""Citations carried on the paper. ``citation_count`` reports the full number beside them."""

_GROUP_MEANINGS: Final = ("cost_center", "fte_classification", "company_code", "staffing_type")
"""Meanings worth grouping a row-level browse by, when the working binds them. A small,
conventional list rather than a per-type declaration - the same approach the evidence
appropriateness check takes for identity columns - since nothing here assumes which of them
a given JE type's table actually carries."""

CITATION_POOL: Final = 24
"""Citations *held per kind* while the pass runs, from which the paper's are chosen.

Bounded on purpose, and it is the failing review this protects. A workbook whose formula
does not hold - or whose required column is empty - produces a citation on every record, and
building 37,613 of them to print twelve costs tens of megabytes and the time to allocate
them, precisely when something has already gone wrong. The pool keeps the largest of each
kind and discards the rest as it goes; ``citation_count``, ``row_impact`` and the row
counters are exact either way, because they are counters rather than collections.
"""

_ROUNDING: Final[dict[RoundingMode, str]] = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.DOWN: ROUND_DOWN,
}

_ZERO: Final = Decimal(0)
_BLANK: Final = Cell()


def recalculate(
    ctx: ToolContext, spec: RecalculationSpec
) -> tuple[EntryRecalculation, tuple[RecalcRow, ...]]:
    """Regenerate the entry under ``spec`` and report everything the pass measured.

    Raises ``ToolError`` only where the workbook cannot support the attempt at all - the
    working itself is not bound, or its sheet is not in the file. Every other shortfall is
    reported on the work paper, because "this could not be established" is a result.

    The second half of the pair is every record the pass walked, kept rather than discarded -
    see ``RecalcRow``. The work paper's own ``citations`` stays a bounded, worst-first sample
    for printing; this is the full set, for a reviewer to browse.
    """
    started = time.monotonic()
    bound = ctx.table(spec.table)
    sheet = ctx.data.sheet(bound.ref.sheet)
    if sheet is None:  # pragma: no cover - ctx.table has already proved the sheet exists
        raise ToolError(f"table {spec.table!r} names a sheet that is not in the file")

    headers = _headers(sheet, bound)
    declared = _readings(spec, bound, headers)
    labels = {reading.id: reading.header or reading.id for reading in declared}
    # What a reader sees every name as. An input shows as the header the preparer wrote; a
    # derived term has no column, so it shows as the short name the business gave the stage.
    # Without the second half an expression comes out half in the preparer's words and half
    # in the machine's - "Total Provision CP + rtt_amount / 12" - and a sentence like that
    # reads as a bug rather than as arithmetic. Both halves are names somebody wrote down,
    # so this is still a substitution and never a translation.
    display = {**labels, **{term.id: term.label for term in spec.derive if term.label}}
    basis = _basis(spec, bound, display, headers)
    booked = _booked(ctx, spec)

    unbound = [reading.id for reading in declared if reading.required and not reading.bound]
    if unbound:
        # The formula cannot be evaluated at all, so nothing is recomputed and nothing is
        # compared. Inconclusive, with the inputs still on the paper so a reader sees which
        # column to go and find rather than being told the entry failed.
        return (
            _inconclusive(
                basis,
                declared,
                audit=audit_formulas(ctx, ctx.formula_shapes, _reperformance(ctx), spec),
                booked=booked,
                note=(
                    f"{_join(unbound)} is not bound in this workbook, so the approved formula "
                    "could not be evaluated and the entry was not recalculated."
                ),
                started=started,
            ),
            (),
        )

    found = _walk(spec, bound, sheet, declared, labels)
    readings = _with_counts(declared, found)
    # After the computation, not before it: the question "is this file arriving at the
    # figure the approved way" is only worth asking once there is a figure to ask it about.
    audit = audit_formulas(ctx, ctx.formula_shapes, _reperformance(ctx), spec)
    by_meaning = {meaning: headers.get(letter, "") for meaning, letter in bound.ref.columns.items()}
    paper = _finish(
        spec, basis, readings, found, booked, by_meaning, audit, display, started=started
    )
    return paper, tuple(found.full_rows)


# --------------------------------------------------------------------------- the pass


@dataclass(slots=True)
class _Pass:
    """What one walk over the records measured. Counters, not conclusions."""

    rows_total: int = 0
    rows_recomputed: int = 0
    rows_incomplete: int = 0
    rows_agreeing: int = 0
    recomputed: Decimal = _ZERO
    row_impact: Decimal = _ZERO
    citation_count: int = 0
    pools: dict[DiscrepancyKind, list[tuple[Decimal, int, RecalcCitation]]] = field(
        default_factory=dict
    )
    """A bounded worst-first heap per kind of discrepancy. See ``CITATION_POOL``."""
    seen: int = 0
    """Citations offered, used only to keep the heap entries unique and comparable."""
    present: dict[str, int] = field(default_factory=dict)
    """Input id -> records holding a number for it."""
    term_totals: dict[str, Decimal] = field(default_factory=dict)
    term_rows: dict[str, int] = field(default_factory=dict)
    term_compared: dict[str, int] = field(default_factory=dict)
    term_agreeing: dict[str, int] = field(default_factory=dict)
    term_impact: dict[str, Decimal] = field(default_factory=dict)
    term_worst: dict[str, tuple[Decimal, int]] = field(default_factory=dict)
    """Term id -> the largest single difference and the row it was on."""
    full_rows: list[RecalcRow] = field(default_factory=list)
    """Every record the pass walked, kept rather than pooled. See ``RecalcRow``."""

    def cite(self, citation: RecalcCitation) -> None:
        """Offer one citation to its kind's pool, keeping the largest and counting them all.

        The sequence number is what makes the heap entries comparable: two citations of the
        same magnitude would otherwise be ordered by comparing the models themselves, which
        a frozen pydantic model cannot do.
        """
        self.citation_count += 1
        self.seen += 1
        entry = (_magnitude(citation), self.seen, citation)
        pool = self.pools.setdefault(citation.kind, [])
        if len(pool) < CITATION_POOL:
            heapq.heappush(pool, entry)
        elif entry > pool[0]:
            heapq.heapreplace(pool, entry)

    @property
    def citations(self) -> list[RecalcCitation]:
        """Everything the pools kept, worst first across every kind."""
        entries = [entry for pool in self.pools.values() for entry in pool]
        entries.sort(reverse=True)
        return [citation for _, _, citation in entries]


def _walk(
    spec: RecalculationSpec,
    bound: BoundTable,
    sheet: Sheet,
    readings: Sequence[RecalcInputReading],
    labels: Mapping[str, str],
) -> _Pass:
    """The single pass. Everything around it is addressing and arithmetic.

    The row mapping is allocated once and overwritten per record: the compiled formulas read
    it synchronously and keep no reference to it, and 37,613 short-lived dicts is work that
    buys nothing.
    """
    cells = sheet.cells
    columns = {
        reading.id: column_index(reading.column)
        for reading in readings
        if reading.bound and reading.column
    }
    constants: dict[str, Decimal | None] = {
        item.id: item.value for item in spec.inputs if item.value is not None
    }
    terms = [(term, compile_formula(term.expression, spec.names)) for term in spec.derive]
    formula = compile_formula(spec.formula, spec.names)

    key_letter = bound.ref.columns.get(spec.record_key)
    key_index = column_index(key_letter) if key_letter else 0
    stated_letter = bound.ref.columns.get(spec.stated) if spec.stated else None
    stated_index = column_index(stated_letter) if stated_letter else 0
    against = {
        term.id: column_index(letter)
        for term, _ in terms
        if term.agrees_with and (letter := bound.ref.columns.get(term.agrees_with))
    }
    group_columns = {
        meaning: column_index(bound.ref.columns[meaning])
        for meaning in _GROUP_MEANINGS
        if bound.has(meaning)
    }

    quantum = Decimal(1).scaleb(-spec.rounding.decimals)
    rounding = _ROUNDING[spec.rounding.mode]
    headroom = spec.row_tolerance
    found = _Pass(present=dict.fromkeys(columns, 0))
    row_values: dict[str, Decimal | None] = {}

    for row in bound.rows:
        if key_index and not _text(cells, row, key_index):
            # Not a record: the blank and subtotal rows a profiled block reaches into. On one
            # sample working the row below the last employee restates the whole entry.
            continue
        found.rows_total += 1

        row_values.clear()
        row_values.update(constants)
        missing: list[str] = []
        for input_id, index in columns.items():
            value = _number(cells, row, index)
            row_values[input_id] = value
            if value is None:
                missing.append(input_id)
            else:
                found.present[input_id] += 1

        for term, compiled in terms:
            value = compiled.evaluate(row_values)
            row_values[term.id] = value
            if value is None:
                continue
            found.term_totals[term.id] = found.term_totals.get(term.id, _ZERO) + value
            found.term_rows[term.id] = found.term_rows.get(term.id, 0) + 1
            agrees_index = against.get(term.id)
            if agrees_index:
                _corroborate(found, term.id, value, cells, row, agrees_index, headroom)

        row_inputs = _cited(row, labels, readings, columns, cells)
        row_group = _group_values(cells, row, group_columns)
        row_terms = _cited_terms(row, row_values, terms, against)

        amount = formula.evaluate(row_values)
        if amount is None:
            found.rows_incomplete += 1
            found.cite(
                _missing_citation(sheet.name, row, missing, labels, readings, columns, cells)
            )
            found.full_rows.append(
                RecalcRow(
                    row=row,
                    group=row_group,
                    kind=DiscrepancyKind.INPUT_MISSING,
                    inputs=row_inputs,
                    terms=row_terms,
                )
            )
            continue

        amount = amount.quantize(quantum, rounding=rounding)
        found.recomputed += amount
        found.rows_recomputed += 1

        if stated_index == 0:
            found.full_rows.append(
                RecalcRow(
                    row=row,
                    group=row_group,
                    recomputed=amount,
                    inputs=row_inputs,
                    terms=row_terms,
                )
            )
            continue
        stated = _number(cells, row, stated_index)
        cell = f"{stated_letter}{row}"
        if stated is None:
            found.cite(
                RecalcCitation(
                    row=row,
                    sheet=sheet.name,
                    kind=DiscrepancyKind.STATED_MISSING,
                    recomputed=amount,
                    stated_cell=cell,
                    why=(
                        "The formula produced an amount for this record and the workbook "
                        "states none of its own to compare it against."
                    ),
                    inputs=row_inputs,
                )
            )
            found.full_rows.append(
                RecalcRow(
                    row=row,
                    group=row_group,
                    recomputed=amount,
                    stated_cell=cell,
                    kind=DiscrepancyKind.STATED_MISSING,
                    inputs=row_inputs,
                    terms=row_terms,
                )
            )
            continue

        difference = amount - stated
        if abs(difference) <= headroom:
            found.rows_agreeing += 1
            found.full_rows.append(
                RecalcRow(
                    row=row,
                    group=row_group,
                    recomputed=amount,
                    stated=stated,
                    stated_cell=cell,
                    difference=difference,
                    difference_pct=_percent(difference, stated),
                    inputs=row_inputs,
                    terms=row_terms,
                )
            )
            continue
        found.row_impact += abs(difference)
        found.cite(
            RecalcCitation(
                row=row,
                sheet=sheet.name,
                kind=DiscrepancyKind.STATED_DIFFERS,
                recomputed=amount,
                stated=stated,
                stated_cell=cell,
                difference=difference,
                difference_pct=_percent(difference, stated),
                why=_differs_why(difference, stated, headroom, quantum, rounding),
                inputs=row_inputs,
            )
        )
        found.full_rows.append(
            RecalcRow(
                row=row,
                group=row_group,
                recomputed=amount,
                stated=stated,
                stated_cell=cell,
                difference=difference,
                difference_pct=_percent(difference, stated),
                kind=DiscrepancyKind.STATED_DIFFERS,
                inputs=row_inputs,
                terms=row_terms,
            )
        )

    return found


def _corroborate(
    found: _Pass,
    term_id: str,
    value: Decimal,
    cells: Mapping[tuple[int, int], Cell],
    row: int,
    index: int,
    headroom: Decimal,
) -> None:
    """Compare one re-performed term against the workbook's own answer for that quantity."""
    stated = _number(cells, row, index)
    if stated is None:
        return
    found.term_compared[term_id] = found.term_compared.get(term_id, 0) + 1
    difference = abs(value - stated)
    if difference <= headroom:
        found.term_agreeing[term_id] = found.term_agreeing.get(term_id, 0) + 1
        return
    found.term_impact[term_id] = found.term_impact.get(term_id, _ZERO) + difference
    worst = found.term_worst.get(term_id)
    if worst is None or difference > worst[0]:
        found.term_worst[term_id] = (difference, row)


# --------------------------------------------------------------------------- readings


def _headers(sheet: Sheet, bound: BoundTable) -> dict[str, str]:
    """Column letter -> the header text the preparer wrote above it."""
    headers: dict[str, str] = {}
    for letter in bound.ref.columns.values():
        value = sheet.cell(bound.ref.header_row, column_index(letter)).value
        if value is not None and str(value).strip():
            headers[letter] = str(value).strip()
    return headers


def _readings(
    spec: RecalculationSpec, bound: BoundTable, headers: Mapping[str, str]
) -> list[RecalcInputReading]:
    """Every declared input against what this workbook actually carries."""
    return [
        RecalcInputReading(
            id=item.id,
            meaning=item.meaning or "",
            unit=item.unit,
            source=item.source or (spec.table if item.meaning else "the approved methodology"),
            description=_input_description(item),
            required=item.required,
            bound=item.value is not None or bool(bound.ref.columns.get(item.meaning or "")),
            column=bound.ref.columns.get(item.meaning or "") or "",
            header=headers.get(bound.ref.columns.get(item.meaning or "") or "", ""),
        )
        for item in spec.inputs
    ]


def _with_counts(
    readings: Sequence[RecalcInputReading], found: _Pass
) -> tuple[RecalcInputReading, ...]:
    """The declared inputs with what the pass found for each. The models are frozen."""
    return tuple(
        reading
        if reading.id not in found.present
        else reading.model_copy(
            update={
                "rows_present": found.present[reading.id],
                "rows_missing": max(found.rows_total - found.present[reading.id], 0),
            }
        )
        for reading in readings
    )


def _input_description(item: RecalcInputSpec) -> str:
    if item.description:
        return item.description
    if item.value is not None:
        return f"a constant the business states: {item.value}"
    return ""


def _basis(
    spec: RecalculationSpec,
    bound: BoundTable,
    labels: Mapping[str, str],
    headers: Mapping[str, str],
) -> RecalcBasis:
    stated = bound.ref.columns.get(spec.stated or "") or ""
    return RecalcBasis(
        table=spec.table,
        sheet=bound.ref.sheet,
        header_row=bound.ref.header_row,
        first_row=bound.ref.first_row,
        last_row=bound.ref.last_row,
        record_key=spec.record_key if bound.has(spec.record_key) else "",
        stated_column=stated,
        stated_header=headers.get(stated, ""),
        formula=spec.formula,
        # Two forms, and the reader needs both. The expanded one names every column the
        # arithmetic reads, which is the only way a stage like "the rebuilt RTT accrual"
        # stops hiding the four columns behind it; the staged one stays readable. Both are
        # built from this workbook's own headers, so both follow the file.
        formula_expanded=expand(
            spec.formula, [(term.id, term.expression) for term in spec.derive], labels
        ),
        formula_in_headers=substitute(spec.formula, labels),
        sign=spec.sign,
        currency=spec.currency.default,
        rounding=spec.rounding.describe(),
        day_count=spec.day_count,
        tolerance_pct=spec.tolerance_pct,
        row_tolerance=spec.row_tolerance,
        worked_example=spec.worked_example,
        explained=_explained(spec.explained),
        provisional_basis=spec.provisional_basis,
    )


def _explained(spec: RecalcExplainedSpec | None) -> RecalcExplained | None:
    if spec is None:
        return None
    return RecalcExplained(
        intro=spec.intro,
        context=spec.context,
        steps=tuple(
            RecalcExplainedStep(label=step.label, detail=step.detail, example=step.example)
            for step in spec.steps
        ),
        summary=spec.summary,
        caveats=spec.caveats,
    )


# --------------------------------------------------------------------- the booked side


def _booked(ctx: ToolContext, spec: RecalculationSpec) -> BookedAmount:
    """What was actually posted, read from somewhere the recomputation never touched.

    The total is quantised to the currency's own scale, like the recomputed side. A posted
    amount is in cents; summing 6,465 line amounts read out of a workbook leaves a tail
    around 1e-8 that is an artefact of the float they were stored as, and reporting that as
    "0.0000000248 more than was posted" would be a difference a reviewer cannot act on
    sitting where a real one belongs. Anything the business considers material at that scale
    is what ``row_tolerance`` is for.
    """
    wanted = spec.booked
    quantum = Decimal(1).scaleb(-spec.rounding.decimals)
    rounding = _ROUNDING[spec.rounding.mode]
    if wanted.method is BookedMethod.STATED_FACT:
        stated = ctx.facts.get(wanted.fact)
        amount = (
            stated.quantize(quantum, rounding=rounding) if isinstance(stated, Decimal) else None
        )
        name = wanted.fact.replace("_", " ")
        return BookedAmount(
            method=wanted.method,
            total=amount,
            description=(
                f"the {name} the entry states about itself"
                if amount is not None
                else f"the entry states no {name}"
            ),
        )

    table = ctx.optional_table(wanted.table)
    if table is None or not table.has(wanted.amount):
        return BookedAmount(
            method=wanted.method,
            table=wanted.table,
            description=(
                f"the {wanted.table} table is not bound in this workbook, so the amount that "
                "was posted could not be read"
            ),
        )

    total = _ZERO
    lines = 0
    accounts: dict[str, None] = {}
    has_account = table.has(wanted.account)
    for row in table.rows:
        amount = table.number(row, wanted.amount)
        # The credit side is the same money again with the other sign, so only the debits
        # are summed. A line of nothing is not a line.
        if amount is None or amount <= 0:
            continue
        lines += 1
        total += amount
        if has_account:
            account = table.text(row, wanted.account)
            if account:
                accounts[account] = None

    return BookedAmount(
        method=wanted.method,
        total=total.quantize(quantum, rounding=rounding) if lines else None,
        description=f"the debit side of the posted entry, summed from {lines:,} of its own lines",
        table=wanted.table,
        sheet=table.ref.sheet,
        lines=lines,
        accounts=tuple(list(accounts)[:MAX_REPORTED_ACCOUNTS]),
    )


# --------------------------------------------------------------------------- the paper


def _reperformance(ctx: ToolContext) -> ReperformanceSpec | None:
    """The record-level identities, when the entry type states any.

    Read from the context rather than passed in: the audit examines every column the
    business declares arithmetic for, and the identities are most of them.
    """
    spec = ctx.je_type_args.get("reperformance")
    return spec if isinstance(spec, ReperformanceSpec) else None


def _finish(
    spec: RecalculationSpec,
    basis: RecalcBasis,
    readings: tuple[RecalcInputReading, ...],
    found: _Pass,
    booked: BookedAmount,
    by_meaning: Mapping[str, str],
    audit: tuple[tuple[FormulaFinding, ...], int],
    display: Mapping[str, str],
    *,
    started: float,
) -> EntryRecalculation:
    """Turn the pass into the work paper: the comparison, the status and the citations."""
    quantum = Decimal(1).scaleb(-spec.rounding.decimals)
    rounding = _ROUNDING[spec.rounding.mode]
    posted = booked.total
    difference = found.recomputed - posted if posted is not None and found.rows_recomputed else None
    tolerance_amount = (
        (abs(posted) * spec.tolerance_pct / 100).quantize(quantum, rounding=rounding)
        if posted is not None
        else _ZERO
    )
    # Floored at the per-row headroom, so a tiny entry is not held to a tolerance of nothing
    # and a zero booked amount still admits the currency's own rounding.
    allowed = max(tolerance_amount, spec.row_tolerance)
    within = None if difference is None else abs(difference) <= allowed
    complete = found.rows_total > 0 and found.rows_recomputed == found.rows_total
    status = _status(found, within, complete)
    citations = _choose(found.citations, cap=CITATION_CAP)

    return EntryRecalculation(
        basis=basis.model_copy(update={"tolerance_amount": tolerance_amount}),
        status=status,
        explanation=_explain(status, found, booked, difference, within, allowed, spec),
        recomputed_total=found.recomputed,
        booked=booked,
        difference=difference,
        difference_pct=None if difference is None else _percent(difference, booked.total),
        within_tolerance=within,
        rows_total=found.rows_total,
        rows_recomputed=found.rows_recomputed,
        rows_incomplete=found.rows_incomplete,
        rows_agreeing=found.rows_agreeing,
        row_impact=found.row_impact,
        inputs=readings,
        components=_components(spec, found, readings, display),
        corroborations=_corroborations(spec, found, by_meaning),
        citations=citations,
        citation_count=found.citation_count,
        formula_findings=audit[0],
        formula_columns_audited=audit[1],
        notes=_notes(spec, found, booked, readings, audit[0]),
        walkthrough=_walkthrough(spec),
        row_diagnosis=_row_diagnosis(spec, citations, difference),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _walkthrough(spec: RecalculationSpec) -> Walkthrough | None:
    """The business's short form of the formula, carried as written."""
    declared = spec.walkthrough
    if declared is None:
        return None
    return Walkthrough(
        total=declared.total,
        stated_label=declared.stated_label,
        steps=tuple(
            WalkthroughStep(id=step.id, line=step.line, label=step.label)
            for step in declared.steps
        ),
        footnote=declared.footnote,
    )


def _row_diagnosis(
    spec: RecalculationSpec,
    citations: Sequence[RecalcCitation],
    difference: Decimal | None,
) -> RowDiagnosis | None:
    """The largest cited record, traced to the one input that explains it.

    The largest rather than the first: a reviewer handed one record to look at should be
    handed the one carrying the money. Where nothing single explains it, this is ``None``
    and the report shows the citations as it always has.
    """
    cited = [citation for citation in citations if citation.kind == "stated_differs"]
    if not cited:
        return None
    worst = max(cited, key=_magnitude)
    return diagnose_row(spec, worst, entry_difference=difference)


def _status(found: _Pass, within: bool | None, complete: bool) -> Status:
    """The measurement, not a judgement. Step 16 turns the figures into the outcome.

    Inconclusive beats everything: a comparison that could not be made is never a failure,
    and a partial recomputation that happens to agree is never a pass.
    """
    if within is None or not found.rows_recomputed:
        return Status.UNABLE_TO_VALIDATE
    if not within:
        return Status.FAIL
    if not complete or found.citation_count:
        return Status.PARTIAL
    return Status.PASS


def _components(
    spec: RecalculationSpec,
    found: _Pass,
    readings: Sequence[RecalcInputReading],
    display: Mapping[str, str],
) -> tuple[RecalcComponent, ...]:
    """Each derived term, totalled, so the recomputed figure comes apart for a reader.

    ``display`` is the same map the headline formula is written with, so a stage built on an
    earlier stage names it the way the rest of the paper does rather than by its id.
    """
    quantum = Decimal(1).scaleb(-spec.rounding.decimals)
    rounding = _ROUNDING[spec.rounding.mode]
    return tuple(
        RecalcComponent(
            id=term.id,
            label=term.label or term.id,
            description=term.description,
            expression=term.expression,
            in_headers=substitute(term.expression, display),
            unit=term.unit,
            total=found.term_totals.get(term.id, _ZERO).quantize(quantum, rounding=rounding),
            rows_populated=found.term_rows.get(term.id, 0),
        )
        for term in spec.derive
    )


def _corroborations(
    spec: RecalculationSpec, found: _Pass, by_meaning: Mapping[str, str]
) -> tuple[RecalcCorroboration, ...]:
    """Each term the workbook also computes, and whether the two agreed on every row."""
    out: list[RecalcCorroboration] = []
    for term in spec.derive:
        compared = found.term_compared.get(term.id)
        if compared is None or term.agrees_with is None:
            continue
        worst = found.term_worst.get(term.id)
        out.append(
            RecalcCorroboration(
                term=term.id,
                against=term.agrees_with,
                label=by_meaning.get(term.agrees_with) or term.agrees_with,
                rows_compared=compared,
                rows_agreeing=found.term_agreeing.get(term.id, 0),
                impact=found.term_impact.get(term.id, _ZERO),
                first_example=f"row {worst[1]}" if worst else "",
            )
        )
    return tuple(out)


def _choose(citations: Sequence[RecalcCitation], *, cap: int) -> tuple[RecalcCitation, ...]:
    """Worst first, with one of every kind that occurred guaranteed a place.

    Money is the right ranking and the wrong one on its own: an incomplete row has no
    difference to rank by, so a run of large amount differences would push every one of them
    off the paper - and "an input was missing on 400 records" is the finding of the three a
    reviewer can act on fastest.
    """
    if len(citations) <= cap:
        return tuple(citations)

    ranked = sorted(citations, key=_magnitude, reverse=True)
    chosen = ranked[:cap]
    missing_kinds = [
        kind
        for kind in DiscrepancyKind
        if kind not in {citation.kind for citation in chosen}
        and any(citation.kind is kind for citation in ranked)
    ]
    if not missing_kinds:
        return tuple(chosen)

    keep = chosen[: max(cap - len(missing_kinds), 0)]
    examples = [
        next(citation for citation in ranked if citation.kind is kind) for kind in missing_kinds
    ]
    return (*keep, *examples)


def _magnitude(citation: RecalcCitation) -> Decimal:
    return abs(citation.difference) if citation.difference is not None else _ZERO


def _notes(
    spec: RecalculationSpec,
    found: _Pass,
    booked: BookedAmount,
    readings: Sequence[RecalcInputReading],
    findings: Sequence[FormulaFinding] = (),
) -> tuple[str, ...]:
    """What the recalculation could not do. Never left for a reader to infer from a blank."""
    notes: list[str] = []
    if spec.provisional_basis:
        notes.append(
            "The formula and the tolerance on this work paper are provisional: the business "
            "has not signed them off, so agreement here is evidence that the arithmetic "
            "reconciles under this basis rather than confirmation that the basis is right."
        )
    optional = [reading.id for reading in readings if not reading.required and not reading.bound]
    if optional:
        notes.append(
            f"{_join(optional)} {'is' if len(optional) == 1 else 'are'} not present in this "
            "workbook, so the part of the formula that reads "
            f"{'it' if len(optional) == 1 else 'them'} contributed nothing."
        )
    if found.rows_incomplete:
        notes.append(
            f"{found.rows_incomplete:,} of {found.rows_total:,} records were not recomputed, "
            "because an input the formula needs was missing. Nothing was summed for them, so "
            "the recomputed total covers only the records that could be rebuilt."
        )
    if booked.total is None:
        notes.append(
            f"The booked amount could not be read: {booked.description}. The recomputed "
            "figure stands on its own and nothing has been compared against it."
        )
    if findings:
        notes.append(
            f"The workbook's own arithmetic was examined and {len(findings):,} fault(s) were "
            "found in it. They are listed with the cell to open the file at: a figure that "
            "agrees is not evidence that the formula behind it is the approved one."
        )
    return tuple(notes)


def _explain(
    status: Status,
    found: _Pass,
    booked: BookedAmount,
    difference: Decimal | None,
    within: bool | None,
    allowed: Decimal,
    spec: RecalculationSpec,
) -> str:
    """The outcome in one sentence, assembled from the figures and nothing else."""
    recomputed = f"{found.recomputed:,}"
    rows = f"{found.rows_recomputed:,} of {found.rows_total:,} records"

    if within is None:
        return (
            f"Rebuilt {recomputed} from {rows}, with nothing to compare it against: "
            f"{booked.description}."
        )

    booked_text = f"{booked.total:,}" if booked.total is not None else "nothing"
    if not difference:
        sentence = (
            f"Rebuilt from {rows}, the entry comes to {recomputed}, which is the booked "
            f"amount of {booked_text} to the cent ({booked.description})."
        )
    else:
        direction = "more than" if difference > 0 else "less than"
        inside = "inside" if within else "outside"
        sentence = (
            f"Rebuilt from {rows}, the entry comes to {recomputed} against {booked_text} "
            f"booked: {abs(difference):,} {direction} was posted, {inside} the tolerance of "
            f"{allowed:,} ({spec.tolerance_pct}% of the booked amount)."
        )
    if status is Status.PARTIAL:
        if found.rows_incomplete:
            sentence += (
                f" {found.rows_incomplete:,} record(s) could not be rebuilt at all, so the "
                "agreement is partial rather than complete."
            )
        elif found.citation_count:
            sentence += (
                f" {found.citation_count:,} record(s) do not agree with the amount the "
                "workbook states for them, which the citations below set out row by row."
            )
    return sentence


def _inconclusive(
    basis: RecalcBasis,
    readings: Sequence[RecalcInputReading],
    *,
    audit: tuple[tuple[FormulaFinding, ...], int],
    booked: BookedAmount,
    note: str,
    started: float,
) -> EntryRecalculation:
    return EntryRecalculation(
        basis=basis,
        status=Status.UNABLE_TO_VALIDATE,
        explanation=note,
        booked=booked,
        inputs=tuple(readings),
        formula_findings=audit[0],
        formula_columns_audited=audit[1],
        notes=(note,),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# --------------------------------------------------------------------------- cell reading


def _number(cells: Mapping[tuple[int, int], Cell], row: int, column: int) -> Decimal | None:
    """One numeric cell, straight out of the sparse dict. Never coerced from text.

    Read through the dict rather than through ``Sheet.cell`` on purpose: this is called once
    per input per record, and that accessor allocates a blank ``Cell`` for every absent one.
    """
    cell = cells.get((row, column))
    if cell is None:
        return None
    value = cell.value
    return value if isinstance(value, Decimal) else None


def _text(cells: Mapping[tuple[int, int], Cell], row: int, column: int) -> str:
    value = cells.get((row, column), _BLANK).value
    return "" if value is None else str(value).strip()


def _cited(
    row: int,
    labels: Mapping[str, str],
    readings: Sequence[RecalcInputReading],
    columns: Mapping[str, int],
    cells: Mapping[tuple[int, int], Cell],
) -> tuple[CitedValue, ...]:
    """Every input behind one record, with the cell it was read from."""
    return tuple(
        CitedValue(
            id=reading.id,
            label=labels.get(reading.id) or reading.id,
            cell=f"{reading.column}{row}" if reading.column else "",
            value=(_number(cells, row, columns[reading.id]) if reading.id in columns else None),
            unit=reading.unit,
        )
        for reading in readings
        if reading.id in columns or not reading.column
    )


def _group_values(
    cells: Mapping[tuple[int, int], Cell], row: int, group_columns: Mapping[str, int]
) -> dict[str, str]:
    """This row's own value for each grouping dimension the table happens to bind."""
    return {
        meaning: text
        for meaning, index in group_columns.items()
        if (text := _text(cells, row, index))
    }


def _cited_terms(
    row: int,
    row_values: Mapping[str, Decimal | None],
    terms: Sequence[tuple[DerivedTermSpec, CompiledFormula]],
    against: Mapping[str, int],
) -> tuple[CitedValue, ...]:
    """Each derived term's own value on this row, in the same shape as an input.

    A derived term is computed, not read from a column, so it has no cell of its own - only
    the column it is corroborated against does, where one exists. Terms the formula could not
    evaluate on this row are left out rather than cited as zero.
    """
    cited: list[CitedValue] = []
    for term, _compiled in terms:
        value = row_values.get(term.id)
        if value is None:
            continue
        agrees_index = against.get(term.id)
        cited.append(
            CitedValue(
                id=term.id,
                label=term.description or term.id,
                cell=f"{column_letter(agrees_index)}{row}" if agrees_index else "",
                value=value,
                unit=term.unit,
            )
        )
    return tuple(cited)


def _missing_citation(
    sheet: str,
    row: int,
    missing: Sequence[str],
    labels: Mapping[str, str],
    readings: Sequence[RecalcInputReading],
    columns: Mapping[str, int],
    cells: Mapping[tuple[int, int], Cell],
) -> RecalcCitation:
    # Only the inputs the formula cannot do without are named as the reason. A declared
    # input may be optional because a blank is the business's own answer - an employee with
    # no RTT entitlement has no entitlement column - and those are blank on thousands of
    # records that rebuild perfectly well. Listing them beside the one that actually stopped
    # this row would point a reviewer at columns there is nothing wrong with. Where nothing
    # required is missing, every blank is named: the row did fail to rebuild, and a citation
    # that names no column at all is worse than one naming too many.
    required = {reading.id for reading in readings if reading.required}
    blocking = [name for name in missing if name in required] or list(missing)
    named = _join([labels.get(name) or name for name in blocking]) or "an input the formula needs"
    return RecalcCitation(
        row=row,
        sheet=sheet,
        kind=DiscrepancyKind.INPUT_MISSING,
        why=(
            f"{named} {'holds' if len(blocking) == 1 else 'hold'} no number on this row, so "
            "the record could not be rebuilt. Nothing was summed for it: reading the gap as a "
            "zero would turn a missing input into a confident amount."
        ),
        inputs=_cited(row, labels, readings, columns, cells),
        missing=tuple(blocking),
    )


def _differs_why(
    difference: Decimal, stated: Decimal, headroom: Decimal, quantum: Decimal, rounding: str
) -> str:
    """Why one row is on the paper, with both figures at the scale the business declared.

    Quantised here as well as in the arithmetic: a Decimal read out of a workbook keeps the
    scale the cell was written at, so an unformatted sentence said "150,000.00 more than the
    12,556.5 this row states" beside a table rendering the same figure as 12,556.50. Two
    scales in one row reads as two different numbers.
    """
    direction = "more" if difference > 0 else "less"
    size = abs(difference).quantize(quantum, rounding=rounding)
    says = stated.quantize(quantum, rounding=rounding)
    return (
        f"The approved formula gives {size:,} {direction} than the {says:,} this row states, "
        f"which is beyond the {headroom} of rounding the business allows a row."
    )


def _percent(difference: Decimal, against: Decimal | None) -> float | None:
    if against is None or not against:
        return None
    return float(difference / against * 100)


def _join(names: Sequence[str]) -> str:
    items = [name for name in names if name]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


__all__ = ["CITATION_CAP", "recalculate"]
