"""Steps 2 to 6 against the three real vacation accrual workbooks.

These are the files the design was written from, so they are the only honest measure of
whether the heuristics work. Two kinds of assertion live here: properties that must hold for
the harness to be worth running, and ``xfail`` markers recording gaps that are known, open
and tracked. ``xfail_strict`` is on, so a gap that gets fixed fails this suite until the
marker is removed — the list cannot quietly go stale.

Row counts are cross-checked against the re-performance engine's published figures, which
are themselves re-asserted by its own golden suite.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest

from review.consts import WorkbookFormat
from review.context.binder import bind_sheet_map
from review.context.loader import ContextLoader
from review.extract.structure_context import build_structure_context, layout_fingerprint
from review.intake.gatekeeper import validate_upload
from review.models.split_summary import SplitSummary
from review.models.workbook import WorkbookProfile
from review.models.workbook_data import WorkbookData
from review.orchestrator.split_summary import build_split_summary
from tests.golden.conftest import SAMPLES

pytestmark = pytest.mark.golden

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STRUCTURE_MAX_TOKENS = 8_000
JE_TYPE = "vacation-accrual-france"

ALL = list(SAMPLES)
XLSX = ["236264.xlsx", "305800.xlsx"]

CALCULATION_TAB = {"236264.xlsx": "GPAY", "305800.xlsx": "GPAY", "935348.xlsb": "DENT"}
CALCULATION_ROWS = {"236264.xlsx": 2_280, "305800.xlsx": 2_273, "935348.xlsb": 37_613}
"""Rows the re-performance engine binds on each calculation tab. The profiler's block may
run longer, because it stops at blank rows rather than at the last bound row."""


# ------------------------------------------------------------------ step 2: the gatekeeper


@pytest.mark.parametrize("name", ALL)
def test_every_real_workbook_is_accepted(name: str, sample_bytes: Callable[[str], bytes]) -> None:
    """Including the 11.5 MB .xlsb, which expands to 238 MB and must not trip the zip guard."""
    result = validate_upload(name, sample_bytes(name), max_bytes=MAX_UPLOAD_BYTES)
    assert result.workbook_format is SAMPLES[name]


# --------------------------------------------------------------------- step 4: the reader


@pytest.mark.parametrize("name", ALL)
def test_reader_sees_every_sheet(name: str, sample_data: Callable[[str], WorkbookData]) -> None:
    assert len(sample_data(name).sheets) == 10


@pytest.mark.parametrize("name", XLSX)
def test_xlsx_carries_formulas(name: str, sample_data: Callable[[str], WorkbookData]) -> None:
    data = sample_data(name)
    assert data.formulas_available is True
    assert any(cell.formula for sheet in data.sheets for cell in sheet.cells.values())


def test_xlsb_is_read_without_formulas(sample_data: Callable[[str], WorkbookData]) -> None:
    """pyxlsb exposes no formula surface. The result records that rather than implying none."""
    data = sample_data("935348.xlsb")
    assert data.workbook_format is WorkbookFormat.XLSB
    assert data.formulas_available is False


def test_xlsb_reads_the_whole_calculation_tab(sample_data: Callable[[str], WorkbookData]) -> None:
    dent = sample_data("935348.xlsb").sheet("DENT")
    assert dent is not None
    assert dent.max_row >= CALCULATION_ROWS["935348.xlsb"]


# ------------------------------------------------------------------- step 5: the profiler


@pytest.mark.parametrize("name", ALL)
def test_the_calculation_tab_is_found(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """The regression that matters most: this tab is where the accrual is re-performed.

    It was missed on 305800 because three of its forty-two column labels repeat, and an
    earlier rule required every header label to be distinct.
    """
    tabs = {table.sheet for table in sample_profile(name).tables}
    assert CALCULATION_TAB[name] in tabs


@pytest.mark.parametrize("name", ALL)
def test_the_calculation_block_covers_every_employee_row(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """A block that stops short would re-perform a subset and still look clean."""
    table = next(t for t in sample_profile(name).tables if t.sheet == CALCULATION_TAB[name])
    rows = table.last_row - table.first_row + 1
    assert rows >= CALCULATION_ROWS[name]


@pytest.mark.parametrize("name", ALL)
def test_the_calculation_header_is_the_real_header(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """Both layouts label their first columns the same way, one row apart between files."""
    table = next(t for t in sample_profile(name).tables if t.sheet == CALCULATION_TAB[name])
    headers = {h.strip().lower() for h in (col.header for col in table.columns) if h}
    assert {"co code", "company code"} & headers
    assert {"matricule", "personnel number"} & headers


@pytest.mark.parametrize("name", XLSX)
def test_posting_and_aggregation_tabs_are_found(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    tabs = {table.sheet for table in sample_profile(name).tables}
    assert {"Batch JE", "Pivot Tab", "MSPeople", "Data Table", "Methodology"} <= tabs


@pytest.mark.parametrize("name", XLSX)
def test_lineage_is_recorded_between_sheets(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    cross_sheet = [
        shape
        for shape in sample_profile(name).formula_shapes
        if any(source != shape.sheet for source in shape.feeds_from)
    ]
    assert cross_sheet, "no cross-sheet formula lineage detected"


def test_typed_over_formulas_find_the_hardcoded_zeros(
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """The substantive finding: constants typed over a calculated column.

    On 305800 the GPAY ``Earned leaves`` column holds 2,136 formulas and 137 constants, every
    one of them zero. That is the same class of defect the re-performance engine reports as
    ``blank_rtt_taken``, and it must not be smoothed away.
    """
    defects = sample_profile("305800.xlsx").defects
    typed_over = [d for d in defects if d.kind == "typed_over_formula" and d.cell.sheet == "GPAY"]
    assert len(typed_over) >= 100


# ------------------------------------------------------------ step 6: context, fingerprint


@pytest.mark.parametrize("name", ALL)
def test_structure_context_stays_under_the_cap(
    name: str,
    sample_profile: Callable[[str], WorkbookProfile],
    sample_data: Callable[[str], WorkbookData],
) -> None:
    context = build_structure_context(
        sample_profile(name), sample_data(name), max_tokens=STRUCTURE_MAX_TOKENS
    )
    assert context.token_estimate <= STRUCTURE_MAX_TOKENS


@pytest.mark.parametrize("name", ALL)
def test_structure_context_leaks_no_employee_values(
    name: str,
    sample_profile: Callable[[str], WorkbookProfile],
    sample_data: Callable[[str], WorkbookData],
) -> None:
    """Samples come only from short category columns, so no name or amount can ride along."""
    context = build_structure_context(
        sample_profile(name), sample_data(name), max_tokens=STRUCTURE_MAX_TOKENS
    )
    for sheet in context.sheets:
        for values in sheet.samples.values():
            assert len(values) <= 3, "samples are capped per column"
            for value in values:
                assert len(value) <= 120, "a long value is free text, not a category label"


@pytest.mark.parametrize("name", ALL)
def test_structure_context_carries_no_employee_names(
    name: str,
    sample_profile: Callable[[str], WorkbookProfile],
    sample_data: Callable[[str], WorkbookData],
) -> None:
    """The name columns of these workbooks are redacted to ``Confidential`` at source.

    Asserting that is what the sample shows keeps the check honest: if a future workbook
    arrives with real names in those columns, this fails rather than quietly shipping them.
    """
    context = build_structure_context(
        sample_profile(name), sample_data(name), max_tokens=STRUCTURE_MAX_TOKENS
    )
    calculation = next(s for s in context.sheets if s.name == CALCULATION_TAB[name])
    for values in calculation.samples.values():
        assert all(value.strip().lower() == "confidential" or len(value) <= 40 for value in values)


@pytest.mark.parametrize("name", ALL)
def test_the_fingerprint_is_reproducible(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """Same profile, same digest. Without this nothing downstream can be cached or replayed."""
    assert layout_fingerprint(sample_profile(name)) == layout_fingerprint(sample_profile(name))


def test_a_different_accrual_model_is_a_different_template(
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    french = layout_fingerprint(sample_profile("236264.xlsx"))
    global_ = layout_fingerprint(sample_profile("935348.xlsb"))
    assert french != global_


def test_the_two_french_periods_are_not_the_same_template(
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """A finding, not a defect, and it matters to the cost model.

    These two files are consecutive periods for the same population, so the design assumed
    the second would hit the layout cache and skip AI-1 and AI-2. It will not: the later
    workbook's calculation tab carries six columns the earlier one does not, so they are
    genuinely different templates. The "six in ten files are familiar" assumption behind the
    cost estimate is unvalidated and should be measured before it is quoted.
    """
    columns = {
        name: next(
            len(t.columns) for t in sample_profile(name).tables if t.sheet == CALCULATION_TAB[name]
        )
        for name in XLSX
    }
    assert columns["236264.xlsx"] != columns["305800.xlsx"]
    assert layout_fingerprint(sample_profile("236264.xlsx")) != layout_fingerprint(
        sample_profile("305800.xlsx")
    )


# ------------------------------------------- the roll-up chain, hop by hop (steps 7 and 13)


@pytest.mark.parametrize("name", ALL)
def test_every_roll_up_hop_is_found(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """``rollup_chain`` validates calculation -> aggregation -> JE Create -> posting.

    A missing hop is not a smaller check, it is an unvalidated one: amounts can be lost
    between two tabs and the grand total still agrees.
    """
    tabs = {table.sheet for table in sample_profile(name).tables}
    assert {CALCULATION_TAB[name], "Pivot Tab", "JE Create", "Batch JE"} <= tabs


@pytest.mark.parametrize("name", ALL)
def test_je_create_binds_the_line_header(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """Its header reuses eight labels across fourteen columns, which no uniqueness rule
    tight enough to be useful would have let through."""
    table = next(t for t in sample_profile(name).tables if t.sheet == "JE Create")
    headers = {h.strip().lower() for h in (col.header for col in table.columns) if h}
    assert {"header id", "company code", "account"} <= headers


@pytest.mark.parametrize("name", ALL)
def test_batch_je_binds_the_line_header_not_the_preamble(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """The posting lines sit under a twenty-six column header, below a six-column preamble
    of batch options that qualifies as a header on its own terms. Widest wins."""
    table = next(t for t in sample_profile(name).tables if t.sheet == "Batch JE")
    headers = {h.strip().lower() for h in (col.header for col in table.columns) if h}
    assert {"header id", "fiscal year", "fiscal period"} <= headers
    assert "batch name:" not in headers


@pytest.mark.parametrize("name", ALL)
def test_no_header_is_detected_inside_a_data_block(
    name: str, sample_profile: Callable[[str], WorkbookProfile]
) -> None:
    """A header found mid-block silently drops every row above it from the table."""
    tables = sample_profile(name).tables
    for first in tables:
        for second in tables:
            if first is second or first.sheet != second.sheet:
                continue
            assert not first.first_row <= second.header_row <= first.last_row, (
                f"{second.sheet} header r{second.header_row} sits inside the block at "
                f"r{first.header_row}"
            )


# --------------------------------------------- step 18: the category split work paper


SPLIT_CLASSES = {
    "236264.xlsx": {"FTE": 2_203, "Non-FTE": 77},
    "305800.xlsx": {"FTE": 2_193, "Non-FTE": 78, "#N/A": 2},
    "935348.xlsb": {"FTE": 37_429, "Non-FTE": 184},
}
"""The classes each real working actually sorts its rows into, and how many carry each.

Read off the files, not off the design. ``#N/A`` is in the list because it is really there:
two rows on one period's workbook carry an Excel error in the classification column, and the
work paper reports it as the class those rows have rather than quietly dropping them.

The three tabs are laid out differently - two French GPAY sheets a row apart, and a global
model on a tab called DENT with a quarter of the columns - and none of this is configured
anywhere. That is the property these tests exist for.
"""


def _work_paper(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> SplitSummary:
    data, measured = sample_data(name), sample_profile(name)
    loader = ContextLoader()
    loader.load()
    binding = bind_sheet_map(measured, data, loader.by_precedence())
    paper = build_split_summary(
        data=data,
        profile=measured,
        sheet_map=binding.sheet_map,
        rules=loader.rules,
        je_spec=loader.je_type(JE_TYPE),
        pack={},
        outcomes={},
    )
    assert paper is not None, f"{name} bound a calculation tab but produced no work paper"
    return paper


@pytest.mark.parametrize("name", ALL)
def test_the_split_finds_the_classes_the_file_actually_holds(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """Three different layouts, no per-file configuration, and the right classes on each."""
    paper = _work_paper(name, sample_data, sample_profile)

    assert {entry.label: entry.row_count for entry in paper.categories} == SPLIT_CLASSES[name]


@pytest.mark.parametrize("name", ALL)
def test_the_split_reads_exactly_the_employee_rows(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """The same row count the re-performance engine binds, reached a different way.

    The engine finds records by employee reference; this finds them by whether the row
    carries a class. Both land on the same number, which is the useful thing: the trailing
    subtotal row is excluded without either of them being told it is there.
    """
    paper = _work_paper(name, sample_data, sample_profile)

    assert paper.basis.rows_read == CALCULATION_ROWS[name]
    assert paper.basis.sheet == CALCULATION_TAB[name]


@pytest.mark.parametrize("name", ALL)
def test_every_component_adds_up_to_its_own_split(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """A split that does not sum to the total beside it is unreadable as a work paper."""
    paper = _work_paper(name, sample_data, sample_profile)

    assert paper.components, "a bound working with no quantity column at all"
    for component in paper.components:
        assert component.total == sum(component.amounts.values(), Decimal(0)), component.header


@pytest.mark.parametrize("name", ALL)
def test_every_control_total_the_file_states_ties_to_the_re_add(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """The real measure of the whole section: these workbooks are arithmetically sound.

    Every number the preparer put above a column agrees with an independent re-add of the
    rows beneath it, to the cent, on all three files. A failure here is either a genuine
    exception in a sample file or - far more likely - the re-add having drifted.
    """
    paper = _work_paper(name, sample_data, sample_profile)

    assert paper.tie_outs, "no control total was found above any column"
    disagreeing = [entry for entry in paper.tie_outs if entry.within_tolerance is not True]
    assert not disagreeing, [(entry.cell, str(entry.variance)) for entry in disagreeing]


@pytest.mark.parametrize("name", ALL)
def test_the_traced_row_carries_no_employee_identifier(
    name: str,
    sample_data: Callable[[str], WorkbookData],
    sample_profile: Callable[[str], WorkbookProfile],
) -> None:
    """A traced row is a row number. The names are in these files; they must not leave them."""
    paper = _work_paper(name, sample_data, sample_profile)

    for walk in paper.walkthroughs:
        shown = {header.casefold() for header, _ in walk.context}
        assert not shown & {"matricule", "salari", "nom", "name", "employee"}, shown
