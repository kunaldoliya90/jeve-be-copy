"""Step 18's work paper: the working re-added from its own rows, split by its own classes.

Everything here is arithmetic over cells plus a projection of results other steps already
reached. No model is involved, which is what lets a reviewer reopen this in March and get the
same figure to the cent.

**Why it is here and not a tool.** A tool produces a ``Figure``: one measurement, an ``ok``,
and an allowlist of numbers a verdict may quote. That is the right shape for *did this hold*
and the wrong shape for *here is the arithmetic* - a 24-column matrix pushed through a
figure's exemplars would be capped by the sample limit and read by every downstream guardrail
as evidence for a conclusion nobody drew. So this sits beside ``diagnosis.py``: a report the
orchestrator assembles at step 18, from the workbook and from the evidence pack.

**What it is careful about.** The exception tests are *carried* from the checks' own figures
and from the statuses step 16 reached, never recomputed. That is the same rule the evidence
register keeps and for the same reason: a second opinion assembled upstream is how a report
comes to disagree with the check it reports on.

Nothing in this module names a row title, a column title or a category. Row titles are the
headers the preparer wrote, column letters come from the verified sheet map, and the categories
are whatever the classification column turns out to hold.
"""

import logging
import re
from decimal import Decimal
from typing import Final

from review.consts import (
    CATEGORY_MAX_DISTINCT,
    CONTROL_TOTAL_MAX_DRIFT,
    FORMULA_SEARCH_ROWS,
    GENERAL_NUMBER_FORMAT,
    HIGH_SEVERITY_WEIGHT,
    IDENTIFIER_DISTINCT_SHARE,
    MAX_SPLIT_CATEGORIES,
    MAX_SPLIT_COMPONENTS,
    MAX_SPLIT_WALKTHROUGHS,
    MAX_WALK_CONTEXT,
)
from review.models.context import OBSERVATION_AREA, BusinessRules, JETypeContext
from review.models.evidence import Figure
from review.models.sheet_map import SheetMap, SheetRole
from review.models.split_summary import (
    CategoryRef,
    ComponentSplit,
    DerivedRule,
    ExceptionTest,
    RowWalkthrough,
    SplitBasis,
    SplitSummary,
    SplitTieOut,
    WalkStep,
)
from review.models.verdicts import CheckOutcome
from review.models.workbook import (
    ColumnProfile,
    ColumnType,
    TableProfile,
    TableRef,
    WorkbookProfile,
)
from review.models.workbook_data import Sheet, WorkbookData
from review.utils.sheet_utils import column_index

logger = logging.getLogger(__name__)

_IDENTIFYING_MEANINGS: Final = frozenset({"employee_ref", "employee_name"})
"""Meanings that identify a person rather than describe a row.

The named half of a two-part guard on the walkthrough. The other half is shape: a column
distinct on nearly every row identifies that row whatever it is called, so a spelling the
alias table has never seen is still excluded. Both have to pass before a column is shown
beside a traced row.
"""

SEVERITY_HIGH: Final = "high"
SEVERITY_MEDIUM: Final = "medium"
SEVERITY_INFO: Final = "info"

_FIRST_ROW_REASON: Final = "The first row of the working, where the rules above were read from"

_WHITESPACE: Final = re.compile(r"\s+")


def _label(header: str) -> str:
    """A header as a row title: the preparer's own words, on one line.

    Column headers in these workbooks carry hard line breaks: "Solde" above "CP N-1" is one
    label the preparer wrapped by hand to fit a column. Collapsing the run of whitespace is a
    formatting change and nothing more - no word is chosen, added or dropped, so the title
    cannot come to say something the file does not.
    """
    return _WHITESPACE.sub(" ", header).strip()


def build_split_summary(
    *,
    data: WorkbookData,
    profile: WorkbookProfile,
    sheet_map: SheetMap,
    rules: BusinessRules,
    je_spec: JETypeContext,
    pack: dict[str, tuple[Figure, ...]],
    outcomes: dict[str, CheckOutcome],
) -> SplitSummary | None:
    """Re-add the working and divide it by the class its own rows carry.

    ``None`` when there is nothing to re-add: no bound table whose rows can be walked, or no
    column holding a small enough set of values to be a classification. Returning an empty
    work paper instead would put a section of dashes under a review that is otherwise sound,
    and a reader cannot tell that apart from a review that failed to compute.
    """
    found = _working_table(sheet_map, profile)
    if found is None:
        logger.info("split summary: no bound table the profiler also measured")
        return None
    name, ref, measured = found

    sheet = data.sheet(ref.sheet)
    if sheet is None:  # pragma: no cover - the map named a sheet the file does not have
        return None

    chosen = _classification(ref, measured)
    if chosen is None:
        logger.info("split summary: %s holds no column with few enough values to split on", name)
        return None

    rows = list(range(ref.first_row, ref.last_row + 1))
    classes = _categories(sheet, rows, chosen)
    if not classes:
        logger.info("split summary: %s!%s is populated on no row", ref.sheet, chosen.letter)
        return None

    columns = _measured_columns(measured, ref)
    components, quantities = _components(
        sheet, rows, columns, ref, chosen, {entry.key for entry in classes}
    )
    headers = _headers_by_sheet(profile)

    notes: list[str] = []
    if quantities > len(components):
        notes.append(
            f"The working holds {quantities:,} columns of quantities. The "
            f"{len(components)} carrying the calculation and the largest totals are shown, in "
            "the file's own column order."
        )
    if not data.formulas_available:
        notes.append(
            "This workbook format carries neither formulas nor cell formats, so how each "
            "column is arrived at could not be read from the file, and a numeric column that "
            "is really a code could not be told apart from a quantity. Every numeric column "
            "is shown. The amounts themselves are unaffected."
        )
    derived_rules = _rules(sheet, ref, components, headers, data.formulas_available)
    if data.formulas_available and components and not derived_rules:
        notes.append(
            "No column of this working calculates itself in the rows scanned: every amount "
            "is a typed value. Nothing can be re-performed from the file's own formulas, "
            "and the arithmetic below was re-added from the values as they stand."
        )
    skipped = len(rows) - sum(entry.row_count for entry in classes)
    if skipped > 0:
        notes.append(
            f"{skipped:,} of the {len(rows):,} rows in the block carry no value in "
            f"{_label(chosen.header)!r} and were not summed. A block ends at a run of blank "
            "rows, so it reaches past the last record into the preparer's own subtotal and "
            "spacer rows."
        )

    return SplitSummary(
        basis=SplitBasis(
            table=name,
            sheet=ref.sheet,
            header_row=ref.header_row,
            first_row=ref.first_row,
            last_row=ref.last_row,
            rows_read=sum(entry.row_count for entry in classes),
            rows_total=len(rows),
            classification_header=_label(chosen.header),
            classification_column=chosen.letter,
            classification_meaning=_meaning_of(ref, chosen.letter),
            tolerance=rules.tolerance,
            formulas_available=data.formulas_available,
        ),
        categories=tuple(classes),
        components=tuple(components),
        tie_outs=_tie_outs(sheet, ref, components, rules, data.formulas_available),
        rules=tuple(derived_rules),
        walkthroughs=_walkthroughs(sheet, ref, measured, components, chosen, headers, pack),
        tests=_tests(je_spec, pack, outcomes),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------- what to re-add


def _working_table(
    sheet_map: SheetMap, profile: WorkbookProfile
) -> tuple[str, TableRef, TableProfile] | None:
    """The employee-level working: the bound table whose rows the split is taken over.

    Chosen by the role the verified map gave its sheet, falling back to the largest bound
    block. Deliberately not by the name the JE type binds it under - that name is business
    configuration, and a type that calls its working something else should not need a code
    change here.
    """
    candidates: list[tuple[str, TableRef, TableProfile]] = []
    for name, ref in sheet_map.tables.items():
        measured = _profile_of(profile, ref)
        if measured is not None and ref.last_row >= ref.first_row:
            candidates.append((name, ref, measured))
    if not candidates:
        return None

    def rank(entry: tuple[str, TableRef, TableProfile]) -> tuple[int, int]:
        _, ref, _ = entry
        # A calculation sheet is where re-performance happens, so its rows are the ones a
        # split is meaningful over. The row count only breaks a tie, or stands in when the
        # map assigned no role at all.
        preferred = sheet_map.roles.get(ref.sheet) is SheetRole.CALCULATION
        return (1 if preferred else 0, ref.last_row - ref.first_row + 1)

    return max(candidates, key=rank)


def _profile_of(profile: WorkbookProfile, ref: TableRef) -> TableProfile | None:
    """The profiler's measurements for a bound table.

    Matched on sheet and header row, which is what the binder bound from, and on the sheet
    alone only as a fallback for a map that came from elsewhere. This is the only source of a
    header for a column the map did *not* bind a meaning to, and those columns are most of a
    real working - so without it the work paper could show only the handful of columns the JE
    type happens to name.
    """
    on_sheet = [table for table in profile.tables if table.sheet == ref.sheet]
    exact = next((table for table in on_sheet if table.header_row == ref.header_row), None)
    if exact is not None:
        return exact
    return max(on_sheet, key=lambda table: len(table.columns), default=None)


def _classification(ref: TableRef, measured: TableProfile) -> ColumnProfile | None:
    """The column the split is taken on: few distinct values, and text rather than amounts.

    A bound meaning wins where there is one, because the map verified it. Failing that the
    column is chosen by shape - the text column with the fewest distinct values above one -
    and ``SplitBasis.classification_meaning`` records which of the two happened, so a reader
    can see when the choice was the fallback.
    """
    usable = [
        column
        for column in measured.columns
        if column.column_type is ColumnType.TEXT
        and column.header
        and column.distinct_count is not None
        and 1 < column.distinct_count <= MAX_SPLIT_CATEGORIES
    ]
    if not usable:
        return None

    named = [column for column in usable if _meaning_of(ref, column.letter) is not None]
    return min(named or usable, key=lambda column: column.distinct_count or 0)


def _meaning_of(ref: TableRef, letter: str) -> str | None:
    return next((meaning for meaning, column in ref.columns.items() if column == letter), None)


def _categories(sheet: Sheet, rows: list[int], chosen: ColumnProfile) -> list[CategoryRef]:
    """The classes the column actually holds, in the order the file first states them.

    Keyed case-folded so ``FTE`` and ``fte `` are one class, labelled by the first spelling
    seen so the work paper reads the way the workbook does.
    """
    index = column_index(chosen.letter)
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for row in rows:
        text = _text(sheet, row, index)
        if not text:
            continue
        key = text.casefold()
        counts[key] = counts.get(key, 0) + 1
        labels.setdefault(key, text)
    return [
        CategoryRef(key=key, label=labels[key], row_count=count) for key, count in counts.items()
    ]


# ---------------------------------------------------------------- the split itself


def _measured_columns(measured: TableProfile, ref: TableRef) -> list[ColumnProfile]:
    """Every numeric column of the working that is worth adding up.

    A numeric column distinct on nearly every row is a key, not a quantity: summing a payroll
    number produces a confident figure that means nothing, so it is excluded by the same shape
    guard the walkthrough uses.
    """
    rows = max(1, ref.last_row - ref.first_row + 1)
    return [
        column
        for column in measured.columns
        if column.column_type is ColumnType.NUMERIC
        and _meaning_of(ref, column.letter) not in _IDENTIFYING_MEANINGS
        and (column.distinct_count or 0) / rows < IDENTIFIER_DISTINCT_SHARE
    ]


def _components(
    sheet: Sheet,
    rows: list[int],
    columns: list[ColumnProfile],
    ref: TableRef,
    chosen: ColumnProfile,
    keys: set[str],
) -> tuple[list[ComponentSplit], int]:
    """Re-add each measured column once, per class and in total, in one pass over the rows.

    Returns the components and how many quantity columns were found, so the caller can say
    whether the cap actually dropped anything rather than guessing from a count that the
    code-versus-quantity test has already changed.

    Kept in the file's own column order: the working reads left to right as a calculation, and
    re-ordering the components by size would hide that. Only the cap drops anything.
    """
    if not columns:
        return [], 0

    class_index = column_index(chosen.letter)
    indexes = {column.letter: column_index(column.letter) for column in columns}
    totalled = _totalled_above(sheet, ref, indexes)
    sums: dict[str, dict[str, Decimal]] = {letter: {} for letter in indexes}
    totals: dict[str, Decimal] = dict.fromkeys(indexes, Decimal(0))
    populated: dict[str, int] = dict.fromkeys(indexes, 0)
    formats: dict[str, str | None] = {}
    fractional: set[str] = set()
    derived: set[str] = set()

    for row in rows:
        key = _text(sheet, row, class_index).casefold()
        if key not in keys:
            # Not a record. The profiled block ends at a run of blank rows, so it reaches
            # past the last employee into the preparer's own subtotal and spacer rows, and
            # on one sample file a trailing total row holds the whole entry a second time.
            # Adding those would double the column; counting them as rows that failed to
            # classify would invent an exception. ``rows_read`` is where the gap is reported.
            continue
        for letter, index in indexes.items():
            cell = sheet.cell(row, index)
            if cell.formula is not None:
                derived.add(letter)
            if not isinstance(cell.value, Decimal):
                continue
            populated[letter] += 1
            totals[letter] += cell.value
            if letter not in formats:
                formats[letter] = cell.number_format
            if letter not in fractional and cell.value != cell.value.to_integral_value():
                fractional.add(letter)
            bucket = sums[letter]
            bucket[key] = bucket.get(key, Decimal(0)) + cell.value

    built = [
        ComponentSplit(
            header=_label(column.header),
            column=column.letter,
            meaning=_meaning_of(ref, column.letter),
            number_format=formats.get(column.letter),
            amounts=sums[column.letter],
            total=totals[column.letter],
            rows_populated=populated[column.letter],
        )
        for column in columns
        if populated[column.letter]
        and _is_quantity(
            formats.get(column.letter),
            has_fraction=column.letter in fractional,
            totalled=column.letter in totalled,
        )
    ]
    if len(built) <= MAX_SPLIT_COMPONENTS:
        return built, len(built)

    # Over the cap, a column a formula computes is kept ahead of a larger one that was typed:
    # the derived columns are the calculation, and magnitude alone keeps four restatements of
    # one provision while dropping the day counts the leave arithmetic runs on. Whatever
    # survives goes back into column order - a work paper sorted by size no longer reads as
    # the calculation it is.
    ranked = sorted(built, key=lambda item: (item.column in derived, abs(item.total)), reverse=True)
    keep = {component.column for component in ranked[:MAX_SPLIT_COMPONENTS]}
    return [component for component in built if component.column in keep], len(built)


def _totalled_above(sheet: Sheet, ref: TableRef, indexes: dict[str, int]) -> set[str]:
    """Columns the preparer put a number above, anywhere between row 1 and the header.

    Presence only - the figure is not compared to anything here, which is what lets this run
    before the rows are added up. ``_tie_outs`` does the comparing afterwards, on the same
    cells.
    """
    return {
        letter
        for letter, index in indexes.items()
        if any(
            isinstance(sheet.cell(row, index).value, Decimal) for row in range(1, ref.header_row)
        )
    }


def _is_quantity(number_format: str | None, *, has_fraction: bool, totalled: bool) -> bool:
    """Whether a numeric column is something to add up rather than a code that happens to be
    numeric.

    The preparer's own formatting is the signal, and it is a reliable one: every amount and
    every day count in these workbooks carries an explicit format, because somebody meant it
    to be read as money or as a quantity. A column left on Excel's default *and* holding only
    whole numbers is a code - the SAP company code is the one such column on the sample files,
    and summing it produces 2,633,130, a confident figure that means nothing and that
    discredits the twenty-three correct rows beside it.

    Both conditions have to hold. A General column with a fraction anywhere in it is a
    quantity somebody forgot to format, which is a very different thing from a key.

    A format of ``None`` is *unreadable*, not General - the binary reader exposes no cell
    formats at all - so for those the signal is the preparer's own control total instead:
    somebody totals a quantity and nobody totals a company code. On the 37,613-row ``.xlsb``
    sample that separates the nine numeric columns exactly, keeping seven quantities and
    dropping the company and cost-centre codes, whose sums are 48 million and 394 *billion*.
    Reading unknown as General instead would drop any whole-number quantity on that format,
    and reading it as a quantity would print those two codes on the work paper.
    """
    if has_fraction:
        return True
    if number_format is None:
        return totalled
    return number_format.strip() != GENERAL_NUMBER_FORMAT


def _tie_outs(
    sheet: Sheet,
    ref: TableRef,
    components: list[ComponentSplit],
    rules: BusinessRules,
    formulas_available: bool,
) -> tuple[SplitTieOut, ...]:
    """Each total the file states above its own header, against a re-add of the rows below it.

    Found by shape rather than by address: a number sitting in a component's column *above*
    the header row is that column's control total, wherever the preparer put it. The nearest
    one above the header wins, because that is the one the header belongs to.

    ``stated_is_formula`` is the finding this section exists for as much as the variance is. A
    control total that was typed rather than calculated agrees with the rows by hand, and will
    go on agreeing after the rows change. It is ``None`` rather than ``False`` on a format
    that carries no formulas: "nobody can tell" must never be reported as "hardcoded".
    """
    found: list[SplitTieOut] = []
    for component in components:
        index = column_index(component.column)
        for row in range(ref.header_row - 1, 0, -1):
            cell = sheet.cell(row, index)
            if not isinstance(cell.value, Decimal):
                continue
            variance = cell.value - component.total
            if not _is_control_total(variance, component.total, rules.tolerance):
                continue
            found.append(
                SplitTieOut(
                    label=component.header,
                    cell=f"{component.column}{row}",
                    stated=cell.value,
                    recomputed=component.total,
                    variance=variance,
                    within_tolerance=abs(variance) <= rules.tolerance,
                    stated_is_formula=(cell.formula is not None) if formulas_available else None,
                )
            )
            break
    return tuple(found)


def _is_control_total(variance: Decimal, recomputed: Decimal, tolerance: Decimal) -> bool:
    """Whether a number above a column is plausibly that column's own total.

    It is if it agrees, and it still is if it is merely close - a stale or hand-typed total is
    off by a rounding or by one period's movement, and finding those is the point. It is not
    if it differs by orders of magnitude: the header band of a real working holds rates,
    counts and per-unit figures, and one of those is a *different fact*, not a control total
    that disagrees. Reporting it as a failed tie-out would be a confident false finding, and
    on one sample file it produced two of them.
    """
    if abs(variance) <= tolerance:
        return True
    if not recomputed:
        return False
    return abs(variance) <= abs(recomputed) * Decimal(str(CONTROL_TOTAL_MAX_DRIFT))


# ---------------------------------------------------------------- how it is calculated


def _headers_by_sheet(profile: WorkbookProfile) -> dict[str, dict[str, str]]:
    """Column letter to header text, per sheet, from the widest table the profiler found.

    The widest table because a sheet's real line header is the one with the most columns - the
    same reason the profiler claims tables widest-header-first. A preamble of batch options
    above the real header would otherwise supply the names a formula is read in.
    """
    widest: dict[str, TableProfile] = {}
    for table in profile.tables:
        held = widest.get(table.sheet)
        if held is None or len(table.columns) > len(held.columns):
            widest[table.sheet] = table
    return {
        name: {column.letter: _label(column.header) for column in table.columns if column.header}
        for name, table in widest.items()
    }


_REFERENCE: Final = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?:(?:'(?P<quoted>[^']+)'|(?P<plain>[A-Za-z_][A-Za-z0-9_.]*))!)?"
    r"\$?(?P<col>[A-Za-z]{1,3})\$?\d+"
    r"(?![A-Za-z0-9_(])"
)
"""An A1 reference, optionally sheet-qualified and with any mix of absolute markers.

The trailing guard is what keeps a function name out: ``LOG10(`` looks exactly like a
reference to column LOG row 10 until you notice the bracket after it.
"""


def resolve_references(
    formula: str, sheet: str, headers: dict[str, dict[str, str]]
) -> tuple[str, tuple[str, ...]]:
    """Rewrite a formula with each reference replaced by the header of the column it reads.

    A substitution, never a translation. ``=IF(AL9="FTE",(AT9/12+AM9),0)`` becomes
    ``=IF([FTE/Non FTE]="FTE",([RTT accrual amount]/12+[Total Provision CP]),0)``, which says
    what the formula does in the preparer's own vocabulary and cannot say anything the file
    does not. Composing prose for an arbitrary formula is the one thing here that could put a
    wrong explanation under a right number, so it is not attempted.

    Text inside string literals is left alone - ``="FTE"`` is a value, not a reference - and a
    column with no header keeps its letters rather than being guessed at.
    """
    reads: list[str] = []

    def swap(match: re.Match[str]) -> str:
        named = match.group("quoted") or match.group("plain") or sheet
        header = headers.get(named, {}).get(match.group("col").upper())
        if not header:
            return match.group(0)
        if header not in reads:
            reads.append(header)
        return f"[{header}]"

    # Excel escapes a quote inside a string by doubling it, so splitting on the quote keeps
    # parity either way: the even-numbered parts are the ones outside a literal.
    parts = formula.split('"')
    for position in range(0, len(parts), 2):
        parts[position] = _REFERENCE.sub(swap, parts[position])
    return '"'.join(parts), tuple(reads)


def _rules(
    sheet: Sheet,
    ref: TableRef,
    components: list[ComponentSplit],
    headers: dict[str, dict[str, str]],
    formulas_available: bool,
) -> list[DerivedRule]:
    """The live formula behind each component, taken off the working's first data row.

    The first row rather than a sampled one: it is the row a reviewer opens the file to, and a
    column whose formula differs further down is a defect the formula-health check reports
    rather than something to average over here.
    """
    if not formulas_available:
        return []

    built: list[DerivedRule] = []
    last = min(ref.last_row, ref.first_row + FORMULA_SEARCH_ROWS - 1)
    for component in components:
        index = column_index(component.column)
        found = next(
            (
                (row, formula)
                for row in range(ref.first_row, last + 1)
                if (formula := sheet.cell(row, index).formula) is not None
            ),
            None,
        )
        if found is None:
            continue
        row, formula = found
        in_headers, reads = resolve_references(formula, ref.sheet, headers)
        built.append(
            DerivedRule(
                column=component.column,
                row=row,
                header=component.header,
                formula=formula,
                in_headers=in_headers,
                reads=reads,
            )
        )
    return built


# ---------------------------------------------------------------- one row, traced


def _walkthroughs(
    sheet: Sheet,
    ref: TableRef,
    measured: TableProfile,
    components: list[ComponentSplit],
    chosen: ColumnProfile,
    headers: dict[str, dict[str, str]],
    pack: dict[str, tuple[Figure, ...]],
) -> tuple[RowWalkthrough, ...]:
    """Trace the first row of the working, then the rows the checks found worst.

    The first row is the exemplar - it is where the rules above were read from, so a reviewer
    can see the arithmetic on a row nothing is wrong with. The rest come from the figures' own
    worst-first exemplars, so every other traced row is one somebody has to look at.
    """
    picked = _rows_to_trace(ref, pack)
    describing = _describing_columns(measured, ref, chosen)
    class_index = column_index(chosen.letter)

    return tuple(
        RowWalkthrough(
            row=row,
            why=why,
            category=_text(sheet, row, class_index),
            context=tuple(
                (_label(column.header), _text(sheet, row, column_index(column.letter)))
                for column in describing
            ),
            steps=tuple(
                _step(sheet, row, component, ref.sheet, headers) for component in components
            ),
        )
        for row, why in picked.items()
    )


def _rows_to_trace(ref: TableRef, pack: dict[str, tuple[Figure, ...]]) -> dict[int, str]:
    """Which rows get traced, and the sentence saying why each one is here.

    **At most one row per check.** Taking exceptions in figure order instead filled every
    slot from the first check that found any - on one live review the leave-balance check had
    1,096 of them, so all five exception rows were the same finding five times and the other
    checks' exceptions were not traced at all. One row each spans the causes, which is what a
    reviewer needs: five rows wrong the same way teach nothing the first does not.

    Insertion-ordered, so the clean exemplar leads and the exceptions follow in the order the
    checks ran. A row outside the bound block is ignored: a figure may cite a cell on another
    tab, and tracing that against this table's columns would produce a row of blanks.
    """
    picked: dict[int, str] = {ref.first_row: _FIRST_ROW_REASON}
    for check_id, figures in pack.items():
        row = next(
            (
                candidate
                for figure in figures
                for sample in figure.samples
                if isinstance(candidate := sample.get("row"), int)
                and candidate not in picked
                and ref.first_row <= candidate <= ref.last_row
            ),
            None,
        )
        if row is None:
            continue
        picked[row] = f"An exception found by {check_id}"
        if len(picked) >= MAX_SPLIT_WALKTHROUGHS:
            break
    return picked


def _describing_columns(
    measured: TableProfile, ref: TableRef, chosen: ColumnProfile
) -> list[ColumnProfile]:
    """Text columns that describe a row without identifying it: a cost centre, a currency.

    Both guards apply. A column the map bound to a person's reference or name is out by name,
    and a column distinct on nearly every row is out by shape - which is what catches a column
    holding something identifying that nobody has taught the alias table about yet.
    """
    rows = max(1, ref.last_row - ref.first_row + 1)
    usable = [
        column
        for column in measured.columns
        if column.column_type is ColumnType.TEXT
        and column.letter != chosen.letter
        and column.header
        and _meaning_of(ref, column.letter) not in _IDENTIFYING_MEANINGS
        and (column.distinct_count or 0) <= CATEGORY_MAX_DISTINCT
        and (column.distinct_count or 0) / rows < IDENTIFIER_DISTINCT_SHARE
    ]
    # A column the map gave a meaning first: a cost centre or a currency says something about
    # the row, where the shape guard alone also admits a redacted column and a concatenated
    # key - both of which pass every test above and tell a reviewer nothing.
    named = [column for column in usable if _meaning_of(ref, column.letter) is not None]
    return (named or usable)[:MAX_WALK_CONTEXT]


def _step(
    sheet: Sheet,
    row: int,
    component: ComponentSplit,
    sheet_name: str,
    headers: dict[str, dict[str, str]],
) -> WalkStep:
    cell = sheet.cell(row, column_index(component.column))
    in_headers = resolve_references(cell.formula, sheet_name, headers)[0] if cell.formula else None
    return WalkStep(
        header=component.header,
        column=component.column,
        cell=f"{component.column}{row}",
        value=cell.value if isinstance(cell.value, Decimal) else None,
        number_format=cell.number_format,
        formula=cell.formula,
        in_headers=in_headers,
    )


def _text(sheet: Sheet, row: int, index: int) -> str:
    value = sheet.cell(row, index).value
    return "" if value is None else str(value).strip()


# ---------------------------------------------------------------- what the tests found


def _tests(
    je_spec: JETypeContext,
    pack: dict[str, tuple[Figure, ...]],
    outcomes: dict[str, CheckOutcome],
) -> tuple[ExceptionTest, ...]:
    """Every check that ran, as a test row. Carried from its figures, never recomputed.

    In the JE type's own order, so this section and the parameters section list the same checks
    in the same sequence. The status is the one step 16 reached: re-deriving it from the
    flagged count here would eventually print a verdict the rest of the report disagrees with,
    which is the failure the evidence register was changed to avoid.
    """
    built: list[ExceptionTest] = []
    for check in je_spec.checks:
        figures = pack.get(check.id, ())
        if not figures:
            continue
        outcome = outcomes.get(check.id)
        decisive = next((figure for figure in figures if figure.decisive), figures[0])
        coverage = next((figure.coverage for figure in figures if figure.coverage), None)
        impact = next(
            (figure.financial_impact for figure in figures if figure.financial_impact), None
        )
        test = ExceptionTest(
            check_id=check.id,
            question=check.question,
            area=check.area,
            tool=decisive.tool,
            rows_total=coverage.rows_total if coverage else 0,
            rows_checked=coverage.rows_bound if coverage else 0,
            flagged=sum(figure.exception_count for figure in figures),
            severity=_severity(check.area, je_spec),
            financial_impact=impact,
            first_example=_first_cell(figures),
            conclusion=decisive.summary,
        )
        built.append(
            test if outcome is None else test.model_copy(update={"status": outcome.final_status})
        )
    return tuple(built)


def _severity(area: str, je_spec: JETypeContext) -> str:
    """How much a flag on this check matters, read off the weight the business gave its area.

    Not a judgement and not a second scale: the weights were confirmed by the business and are
    what the score already runs on, so grading severity from anything else would let the work
    paper and the score tell a reader two different stories about what matters. An unscored
    observation is always the lowest grade, because it moves no score by design.
    """
    if area == OBSERVATION_AREA:
        return SEVERITY_INFO
    spec = je_spec.areas.get(area)
    if spec is None:  # pragma: no cover - the loader rejects a check naming an unknown area
        return SEVERITY_MEDIUM
    return SEVERITY_HIGH if spec.weight >= HIGH_SEVERITY_WEIGHT else SEVERITY_MEDIUM


def _first_cell(figures: tuple[Figure, ...]) -> str | None:
    """The first exemplar's location, which is where a reviewer starts looking."""
    for figure in figures:
        for sample in figure.samples:
            cell = sample.get("cell")
            if isinstance(cell, str) and cell:
                return cell
            row = sample.get("row")
            if isinstance(row, int):
                return f"row {row}"
    return None


__all__ = ["build_split_summary", "resolve_references"]
