"""The category split work paper, pinned on the two properties it exists to keep.

The first is that **nothing is named in code**. Every fixture below spells its classes and
its headers in a way no module mentions, and the assertions are that the work paper comes
back in the file's own vocabulary. A test that asserted ``"FTE"`` would pass just as happily
against a builder with the word hardcoded, which is the thing being guarded against.

The second is that it **never forms its own opinion**. The statuses on the tests section come
from the outcomes handed in; the control totals are only reported where the number above the
column is plausibly that column's total. Both are places where a confident wrong answer is
worse than no answer.
"""

from collections.abc import Mapping
from decimal import Decimal

import pytest

from api.consts import RiskRating
from review.models.context import (
    AreaSpec,
    Band,
    BusinessRules,
    CheckRecipe,
    CheckSpec,
    JETypeContext,
)
from review.models.evidence import Figure, RowCoverage
from review.models.result import ScoringMethod
from review.models.sheet_map import SheetMap, SheetRole
from review.models.split_summary import SplitSummary
from review.models.verdicts import CheckOutcome, Status
from review.models.workbook import (
    ColumnProfile,
    ColumnType,
    SheetProfile,
    TableProfile,
    TableRef,
    WorkbookProfile,
)
from review.models.workbook_data import Cell, CellValue, Sheet, WorkbookData
from review.orchestrator.split_summary import build_split_summary, resolve_references
from review.utils.sheet_utils import SheetUtils, column_index

SHEET = "Feuil1"
"""Deliberately not a name any role or alias would recognise. The split must not depend on
the tab being called anything in particular."""

MONEY = "#,##0.00"
GENERAL = "General"

# The classes this fixture sorts its rows into. Nothing in the harness has been taught these
# words, which is the point of choosing them.
PERMANENT = "Permanent"
CASUAL = "casual"

_RECIPE = CheckRecipe(tool="t")
"""Figures-decided checks are required to state one; which tool it names is irrelevant here."""


def _rules() -> BusinessRules:
    return BusinessRules(
        version=1,
        credits=dict.fromkeys(Status, 0) | {Status.PASS: 100},
        bands=[Band(min=0, label="High risk", risk=RiskRating.HIGH)],
        tolerance=Decimal("0.005"),
    )


def _je_type(*checks: CheckSpec) -> JETypeContext:
    """A minimal type whose weights make the severity grading observable.

    ``accrual`` is weighted above the high-severity threshold and ``hygiene`` below it, so a
    test asserting
    "high" against the first and "medium" against the second is asserting that the grade is
    read off the weight rather than hardcoded per area.
    """
    return JETypeContext(
        id="t",
        name="T",
        version=1,
        objective="o",
        areas={
            "accrual": AreaSpec(weight=90, method=ScoringMethod.WEAKEST_CHECK),
            "hygiene": AreaSpec(weight=10, method=ScoringMethod.EXCEPTION_RATE),
        },
        checks=checks
        or (
            CheckSpec(
                id="a",
                area="accrual",
                question="Does it re-perform?",
                decided_by="figures",
                recipe=_RECIPE,
            ),
            CheckSpec(
                id="h", area="hygiene", question="Is it tidy?", decided_by="figures", recipe=_RECIPE
            ),
        ),
    )


def _sheet(
    rows: list[list[CellValue]],
    *,
    general: frozenset[str] = frozenset({"C"}),
    formats_readable: bool = True,
    formulas: dict[str, str] | None = None,
) -> Sheet:
    """Build a sheet from a grid, formatted the way a preparer would have formatted it.

    ``general`` names the columns left on Excel's default format. It has to be said rather
    than inferred from the Python type: the reader turns every number into a ``Decimal``
    whatever the cell was formatted as, so the format is the only thing left that
    distinguishes a quantity from a code, which is exactly what the builder relies on.
    """
    cells: dict[tuple[int, int], Cell] = {}
    for offset, line in enumerate(rows, start=1):
        for index, value in enumerate(line, start=1):
            if value is None:
                continue
            on_default = SheetUtils.column_letter(index) in general
            # A binary workbook exposes no cell formats at all, which is a different
            # thing from a cell the preparer left on Excel's default.
            number_format = None
            if formats_readable:
                number_format = GENERAL if on_default else MONEY
            cells[(offset, index)] = Cell(value=value, number_format=number_format)
    for ref, formula in (formulas or {}).items():
        letters = "".join(char for char in ref if char.isalpha())
        row = int("".join(char for char in ref if char.isdigit()))
        position = (row, column_index(letters))
        held = cells.get(position, Cell())
        cells[position] = Cell(
            value=held.value, formula=formula, number_format=held.number_format or MONEY
        )
    return Sheet(
        name=SHEET,
        max_row=max((row for row, _ in cells), default=0),
        max_column=max((column for _, column in cells), default=0),
        cells=cells,
    )


# A header at row 2 with a control total above it at row 1, three records, and a trailing
# subtotal row the profiled block reaches into.
#   A: the class            B: an amount        C: a whole-number code      D: a count
_GRID: list[list[CellValue]] = [
    [None, Decimal("600.00"), None, None],
    ["Category", "Provision locale", "Centre", "Jours"],
    [PERMANENT, Decimal("100.00"), Decimal("1073"), Decimal("2.5")],
    [PERMANENT, Decimal("350.00"), Decimal("1073"), Decimal("1.5")],
    [CASUAL, Decimal("150.00"), Decimal("2048"), Decimal("4.0")],
    [None, Decimal("600.00"), None, None],
]

_COLUMNS = (
    ColumnProfile(letter="A", header="Category", column_type=ColumnType.TEXT, distinct_count=2),
    ColumnProfile(
        letter="B", header="Provision locale", column_type=ColumnType.NUMERIC, distinct_count=3
    ),
    ColumnProfile(letter="C", header="Centre", column_type=ColumnType.NUMERIC, distinct_count=2),
    ColumnProfile(letter="D", header="Jours", column_type=ColumnType.NUMERIC, distinct_count=3),
)


def _build(
    *,
    grid: list[list[CellValue]] | None = None,
    general: frozenset[str] = frozenset({"C"}),
    formats_readable: bool = True,
    columns: tuple[ColumnProfile, ...] = _COLUMNS,
    bound: dict[str, str] | None = None,
    formulas: dict[str, str] | None = None,
    formulas_available: bool = True,
    pack: Mapping[str, tuple[Figure, ...]] | None = None,
    outcomes: Mapping[str, CheckOutcome] | None = None,
    je_type: JETypeContext | None = None,
) -> SplitSummary | None:
    sheet = _sheet(
        grid if grid is not None else _GRID,
        general=general,
        formats_readable=formats_readable,
        formulas=formulas,
    )
    ref = TableRef(
        sheet=SHEET,
        header_row=2,
        first_row=3,
        last_row=6,
        columns=bound if bound is not None else {"fte_classification": "A"},
    )
    profile = WorkbookProfile(
        sheets=(SheetProfile(name=SHEET, row_count=sheet.max_row, column_count=sheet.max_column),),
        tables=(TableProfile(sheet=SHEET, header_row=2, first_row=3, last_row=6, columns=columns),),
    )
    return build_split_summary(
        data=WorkbookData(sheets=(sheet,), formulas_available=formulas_available),
        profile=profile,
        sheet_map=SheetMap(roles={SHEET: SheetRole.CALCULATION}, tables={"calculation": ref}),
        rules=_rules(),
        je_spec=je_type or _je_type(),
        pack=dict(pack or {}),
        outcomes=dict(outcomes or {}),
    )


@pytest.fixture
def paper() -> SplitSummary:
    built = _build()
    assert built is not None
    return built


# ------------------------------------------------------------------ nothing is named in code


def test_build_split_summary_takes_its_categories_from_the_file(paper: SplitSummary) -> None:
    """The classes are whatever the column holds, spelled the way the file spells them."""
    assert [entry.label for entry in paper.categories] == [PERMANENT, CASUAL]
    assert [entry.row_count for entry in paper.categories] == [2, 1]


def test_build_split_summary_titles_its_rows_with_the_files_own_headers(
    paper: SplitSummary,
) -> None:
    assert [component.header for component in paper.components] == ["Provision locale", "Jours"]
    assert paper.basis.classification_header == "Category"


def test_build_split_summary_folds_case_so_one_class_is_not_counted_as_two() -> None:
    grid: list[list[CellValue]] = [
        *_GRID[:3],
        [CASUAL, Decimal("350.00"), Decimal("1073"), Decimal("1.5")],
        [CASUAL.upper(), Decimal("150.00"), Decimal("2048"), Decimal("4.0")],
        _GRID[5],
    ]
    paper = _build(grid=grid)

    assert paper is not None
    assert len(paper.categories) == 2, "'casual' and 'CASUAL' are the same class"
    assert [entry.label for entry in paper.categories] == [PERMANENT, CASUAL], (
        "labelled by the first spelling the file uses, not the last"
    )
    assert paper.categories[1].row_count == 2


def test_build_split_summary_collapses_a_header_the_preparer_wrapped_by_hand() -> None:
    columns = (
        _COLUMNS[0],
        _COLUMNS[1].model_copy(update={"header": "Provision\nlocale  N-1"}),
        *_COLUMNS[2:],
    )
    paper = _build(columns=columns)

    assert paper is not None
    assert paper.components[0].header == "Provision locale N-1"


def test_build_split_summary_falls_back_to_shape_when_no_meaning_is_bound() -> None:
    """A workbook the alias table has never seen still gets a work paper, and says so."""
    paper = _build(bound={})

    assert paper is not None
    assert paper.basis.classification_column == "A"
    assert paper.basis.classification_meaning is None, "the fallback has to be visible"


def test_build_split_summary_returns_nothing_when_no_column_can_be_split_on() -> None:
    """A section of dashes is indistinguishable from a section that failed to compute."""
    columns = tuple(
        column.model_copy(update={"column_type": ColumnType.NUMERIC}) for column in _COLUMNS
    )
    assert _build(columns=columns, bound={}) is None


# ------------------------------------------------------------------ the arithmetic


def test_build_split_summary_splits_each_component_by_class(paper: SplitSummary) -> None:
    provision = paper.components[0]

    assert provision.amounts == {
        PERMANENT.casefold(): Decimal("450.00"),
        CASUAL.casefold(): Decimal("150.00"),
    }
    assert provision.total == Decimal("600.00")


def test_build_split_summary_totals_only_what_the_split_accounts_for(paper: SplitSummary) -> None:
    """The split must visibly add up to the total beside it, on every component."""
    for component in paper.components:
        assert component.total == sum(component.amounts.values(), Decimal(0))


def test_build_split_summary_leaves_the_blocks_trailing_subtotal_row_out(
    paper: SplitSummary,
) -> None:
    """The row below the records holds the whole column again. Adding it would double it."""
    assert paper.basis.rows_read == 3
    assert paper.basis.rows_total == 4
    assert paper.components[0].total == Decimal("600.00")
    assert any("not summed" in note for note in paper.notes)


def test_build_split_summary_shares_are_the_class_against_the_component(
    paper: SplitSummary,
) -> None:
    assert paper.components[0].shares == pytest.approx({"permanent": 0.75, "casual": 0.25})


def test_build_split_summary_ignores_a_whole_number_column_excel_never_formatted(
    paper: SplitSummary,
) -> None:
    """Summing a cost-centre code gives a confident figure that means nothing."""
    assert "Centre" not in [component.header for component in paper.components]


def test_build_split_summary_keeps_an_unformatted_column_that_holds_a_fraction() -> None:
    """Both conditions have to hold: a General column with a fraction is a quantity.

    A column somebody forgot to format is a very different thing from a key, and the
    fraction is what tells them apart - nobody books half a cost centre.
    """
    grid = [list(line) for line in _GRID]
    grid[2][2] = Decimal("1073.5")
    paper = _build(grid=grid)

    assert paper is not None
    assert "Centre" in [component.header for component in paper.components]


def test_build_split_summary_keeps_a_whole_number_column_the_preparer_totalled() -> None:
    """Where no format can be read, the preparer's own control total is the signal left.

    A binary workbook exposes no cell formats, so the General test cannot run. Reading
    "unknown" as General dropped two whole-number quantity columns from the 37,613-row
    sample; reading it as a quantity printed a cost-centre code summing to 394 billion.
    Somebody totals a quantity and nobody totals a code, so that is what decides it.
    """
    grid = [list(line) for line in _GRID]
    grid[0][2] = Decimal("4194")  # a control total above the whole-number column
    paper = _build(grid=grid, formats_readable=False)

    assert paper is not None
    assert "Centre" in [component.header for component in paper.components]


def test_build_split_summary_drops_an_untotalled_whole_number_column_with_no_format() -> None:
    paper = _build(formats_readable=False)

    assert paper is not None
    headers = [component.header for component in paper.components]
    assert "Centre" not in headers, "nobody totals a code"
    assert "Provision locale" in headers, "and the quantities are all still there"
    assert "Jours" in headers


def test_build_split_summary_carries_the_files_own_number_format(paper: SplitSummary) -> None:
    """So money, days and percentages render the way the file renders them."""
    assert paper.components[0].number_format == MONEY


# ------------------------------------------------------------------ the control totals


def test_build_split_summary_finds_the_total_the_file_states_above_its_header(
    paper: SplitSummary,
) -> None:
    tie_out = next(entry for entry in paper.tie_outs if entry.label == "Provision locale")

    assert tie_out.cell == "B1"
    assert tie_out.stated == Decimal("600.00")
    assert tie_out.recomputed == Decimal("600.00")
    assert tie_out.within_tolerance is True


def test_build_split_summary_reports_a_control_total_that_was_typed_rather_than_calculated(
    paper: SplitSummary,
) -> None:
    """A hand-typed total agrees with the rows by hand, and goes on agreeing after a change."""
    tie_out = next(entry for entry in paper.tie_outs if entry.cell == "B1")
    assert tie_out.stated_is_formula is False


def test_build_split_summary_never_calls_a_total_hardcoded_on_a_format_with_no_formulas() -> None:
    """ "Nobody can tell" and "typed by hand" are different findings."""
    paper = _build(formulas_available=False)

    assert paper is not None
    assert all(entry.stated_is_formula is None for entry in paper.tie_outs)


def test_build_split_summary_reports_a_stated_total_that_is_merely_close() -> None:
    """A stale or rounded control total is the thing this section is looking for."""
    grid: list[list[CellValue]] = [[None, Decimal("599.50"), None, None], *_GRID[1:]]
    paper = _build(grid=grid)

    assert paper is not None
    tie_out = next(entry for entry in paper.tie_outs if entry.cell == "B1")
    assert tie_out.within_tolerance is False
    assert tie_out.variance == Decimal("-0.50")


def test_build_split_summary_does_not_report_a_number_of_a_different_magnitude() -> None:
    """A rate in the header band is a different fact, not a control total that disagrees.

    Reporting it would be a confident false finding, and on one of the real sample workbooks
    the earlier nearest-number-above rule produced two of them.
    """
    grid: list[list[CellValue]] = [[None, Decimal("17.25"), None, None], *_GRID[1:]]
    paper = _build(grid=grid)

    assert paper is not None
    assert not [entry for entry in paper.tie_outs if entry.label == "Provision locale"]


# ------------------------------------------------------------------ the rules


def test_resolve_references_swaps_each_reference_for_the_column_it_reads() -> None:
    headers = {SHEET: {"AL": "FTE/Non FTE", "AT": "RTT accrual", "AM": "Provision CP"}}

    resolved, reads = resolve_references('=IF(AL9="FTE",(AT9/12+AM9),0)', SHEET, headers)

    assert resolved == '=IF([FTE/Non FTE]="FTE",([RTT accrual]/12+[Provision CP]),0)'
    assert reads == ("FTE/Non FTE", "RTT accrual", "Provision CP")


def test_resolve_references_leaves_text_inside_a_string_literal_alone() -> None:
    """``="B2"`` is a value somebody typed, not a reference to column B."""
    resolved, reads = resolve_references('=IF(A2="B2","B2",0)', SHEET, {SHEET: {"B": "Amount"}})

    assert resolved == '=IF(A2="B2","B2",0)'
    assert reads == ()


def test_resolve_references_does_not_mistake_a_function_name_for_a_reference() -> None:
    """``LOG10(`` is indistinguishable from column LOG row 10 until the bracket."""
    resolved, _ = resolve_references("=LOG10(B2)", SHEET, {SHEET: {"LOG": "x", "B": "Amount"}})

    assert resolved == "=LOG10([Amount])"


def test_resolve_references_handles_absolute_and_sheet_qualified_references() -> None:
    headers = {SHEET: {"B": "Amount"}, "Master": {"C": "Rate"}}

    resolved, reads = resolve_references("=$B$2*Master!C7*'Feuil1'!B3", SHEET, headers)

    assert resolved == "=[Amount]*[Rate]*[Amount]"
    assert reads == ("Amount", "Rate")


def test_resolve_references_keeps_the_letters_of_a_column_with_no_header() -> None:
    """A guessed-at label is exactly the sort of thing a reader would quote in a meeting."""
    resolved, reads = resolve_references("=B2*ZZ9", SHEET, {SHEET: {"B": "Amount"}})

    assert resolved == "=[Amount]*ZZ9"
    assert reads == ("Amount",)


def test_build_split_summary_reads_the_rule_off_the_first_row_that_calculates() -> None:
    """One sample working types over its whole first row, and has 2,136 formulas below it."""
    paper = _build(formulas={"D4": "=A4*2"})

    assert paper is not None
    rule = next(entry for entry in paper.rules if entry.column == "D")
    assert rule.row == 4
    assert rule.header == "Jours"


def test_build_split_summary_says_so_when_no_column_calculates_itself(
    paper: SplitSummary,
) -> None:
    assert paper.rules == ()
    assert any("typed value" in note for note in paper.notes)


def test_build_split_summary_reports_a_format_with_no_formulas_as_unread_not_as_absent() -> None:
    paper = _build(formulas_available=False)

    assert paper is not None
    assert paper.basis.formulas_available is False
    assert any("neither formulas nor cell formats" in note for note in paper.notes)


# ------------------------------------------------------------------ the traced row


def test_build_split_summary_traces_the_first_row_of_the_working(paper: SplitSummary) -> None:
    walk = paper.walkthroughs[0]

    assert walk.row == 3
    assert walk.category == PERMANENT
    assert [step.value for step in walk.steps] == [Decimal("100.00"), Decimal("2.5")]
    assert [step.cell for step in walk.steps] == ["B3", "D3"]


def test_build_split_summary_traces_the_rows_the_checks_found(paper: SplitSummary) -> None:
    figure = Figure(
        figure_id="F-01",
        check_id="a",
        tool="recalc",
        samples=({"row": 5, "difference": Decimal("12")},),
    )
    built = _build(pack={"a": (figure,)})

    assert built is not None
    assert [walk.row for walk in built.walkthroughs] == [3, 5]
    assert "a" in built.walkthroughs[1].why


def test_build_split_summary_traces_at_most_one_row_per_check() -> None:
    """Five rows wrong the same way teach nothing the first one does not.

    Taking exceptions in figure order instead filled every slot from whichever check found
    the most: on a live review the leave-balance check had 1,096 of them, so all five
    exception rows were one finding five times and the negative accrual was never traced.
    """
    many = Figure(
        figure_id="F-01",
        check_id="a",
        tool="recalc",
        samples=tuple({"row": row} for row in (4, 5, 6)),
    )
    other = Figure(figure_id="F-02", check_id="h", tool="scan", samples=({"row": 6},))
    built = _build(pack={"a": (many,), "h": (other,)})

    assert built is not None
    traced = {walk.row: walk.why for walk in built.walkthroughs}
    assert list(traced) == [3, 4, 6], "the exemplar, then one row from each check"
    assert "a" in traced[4]
    assert "h" in traced[6]


def test_build_split_summary_ignores_a_cited_row_outside_the_block() -> None:
    """A figure may cite a cell on another tab; tracing it here is a row of blanks."""
    figure = Figure(figure_id="F-01", check_id="a", tool="recalc", samples=({"row": 900},))
    built = _build(pack={"a": (figure,)})

    assert built is not None
    assert [walk.row for walk in built.walkthroughs] == [3]


def test_build_split_summary_never_shows_a_column_bound_to_a_person(paper: SplitSummary) -> None:
    """Excluded by the meaning the map gave it. A traced row is a row number, not a person."""
    columns = (
        *_COLUMNS,
        ColumnProfile(
            letter="E", header="Matricule", column_type=ColumnType.TEXT, distinct_count=2
        ),
    )
    grid = [[*line, None] if len(line) == 4 else line for line in _GRID]
    grid[2][4] = "10044212"
    built = _build(
        grid=grid,
        columns=columns,
        bound={"fte_classification": "A", "employee_ref": "E"},
    )

    assert built is not None
    assert all(header != "Matricule" for header, _ in built.walkthroughs[0].context)


def test_build_split_summary_never_shows_a_column_distinct_on_nearly_every_row() -> None:
    """The shape half of the guard: a spelling the alias table has never seen fails closed."""
    columns = (
        *_COLUMNS,
        ColumnProfile(
            letter="E", header="Nom complet", column_type=ColumnType.TEXT, distinct_count=4
        ),
    )
    grid = [[*line, None] for line in _GRID]
    for offset, name in enumerate(["A Dupont", "B Martin", "C Bernard", "D Petit"]):
        grid[2 + offset][4] = name
    built = _build(grid=grid, columns=columns)

    assert built is not None
    assert all(header != "Nom complet" for header, _ in built.walkthroughs[0].context)


# ------------------------------------------------------------------ the tests section


def test_build_split_summary_carries_the_status_step_sixteen_reached() -> None:
    """Never re-derived. A work paper with its own opinion eventually contradicts the report."""
    figure = Figure(
        figure_id="F-01",
        check_id="a",
        tool="recalc",
        decisive=True,
        ok=False,
        exception_count=4,
        summary="three rows differ",
        coverage=RowCoverage(rows_total=3, rows_bound=2, rows_tied=1),
        financial_impact=Decimal("42"),
        samples=({"row": 4, "cell": "B4"},),
    )
    outcome = CheckOutcome(check_id="a", area="accrual", final_status=Status.PARTIAL)
    built = _build(pack={"a": (figure,)}, outcomes={"a": outcome})

    assert built is not None
    test = next(entry for entry in built.tests if entry.check_id == "a")
    assert test.status is Status.PARTIAL, "the figure says not-ok; the outcome says partial"
    assert test.flagged == 4
    assert test.rows_total == 3
    assert test.rows_checked == 2
    assert test.financial_impact == Decimal("42")
    assert test.first_example == "B4"
    assert test.conclusion == "three rows differ"
    assert test.question == "Does it re-perform?"


def test_build_split_summary_grades_severity_off_the_weight_the_business_gave_the_area() -> None:
    figures = {
        name: (Figure(figure_id="F-01", check_id=name, tool="t", decisive=True),)
        for name in ("a", "h")
    }
    built = _build(pack=figures)

    assert built is not None
    graded = {entry.check_id: entry.severity for entry in built.tests}
    assert graded == {"a": "high", "h": "medium"}


def test_build_split_summary_grades_an_unscored_observation_as_the_lowest() -> None:
    """It moves no score by design, so it can never be the thing that matters most."""
    je_type = _je_type(
        CheckSpec(id="a", area="accrual", question="q", decided_by="figures", recipe=_RECIPE),
        CheckSpec(id="h", area="hygiene", question="q", decided_by="figures", recipe=_RECIPE),
        CheckSpec(
            id="o",
            area="observation",
            question="Where is personal data?",
            decided_by="figures",
            recipe=_RECIPE,
        ),
    )
    figures = {"o": (Figure(figure_id="F-01", check_id="o", tool="pii_scan", decisive=True),)}
    built = _build(pack=figures, je_type=je_type)

    assert built is not None
    assert next(entry for entry in built.tests if entry.check_id == "o").severity == "info"


def test_build_split_summary_omits_a_check_that_produced_no_figure() -> None:
    """A test row with nothing behind it reads as a test that ran and found nothing."""
    built = _build(pack={})

    assert built is not None
    assert built.tests == ()
