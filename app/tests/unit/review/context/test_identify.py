"""Tier 2: which journal entry type a workbook is, decided by its columns.

The tests that matter most here are the negative ones. A near-miss that binds anyway
re-performs the wrong arithmetic and returns a plausible number, so ``rejects`` and the
unobservable-signal rule are tested harder than the happy path.
"""

from decimal import Decimal

import pytest

from review.context.identify import UNKNOWN, identify
from review.models.context import (
    AreaSpec,
    CheckSpec,
    DecidedBy,
    DiscriminatorSpec,
    JETypeContext,
    VariantSpec,
)
from review.models.result import ScoringMethod
from review.models.workbook import (
    ColumnProfile,
    ColumnType,
    SheetProfile,
    TableProfile,
    WorkbookProfile,
)
from review.models.workbook_data import Cell, Sheet, WorkbookData


def _column(letter: str, header: str) -> ColumnProfile:
    return ColumnProfile(
        letter=letter, header=header, column_type=ColumnType.NUMERIC, total=Decimal(1)
    )


def _table(sheet: str, headers: dict[str, str], rows: int = 5) -> TableProfile:
    return TableProfile(
        sheet=sheet,
        header_row=1,
        first_row=2,
        last_row=1 + rows,
        columns=tuple(_column(letter, header) for letter, header in headers.items()),
    )


def _workbook(
    *tables: TableProfile, empty: frozenset[tuple[str, str]] = frozenset()
) -> tuple[WorkbookProfile, WorkbookData]:
    """A profile and a matching data object where every bound cell carries a value.

    ``empty`` names ``(sheet, header)`` pairs to leave unpopulated, which is how a column
    that exists but holds nothing is modelled — the difference between a reversing accrual
    and a template.
    """
    cells: dict[str, dict[tuple[int, int], Cell]] = {}
    extent: dict[str, tuple[int, int]] = {}

    for table in tables:
        sheet_cells = cells.setdefault(table.sheet, {})
        for index, column in enumerate(table.columns, start=1):
            if (table.sheet, column.header) in empty:
                continue
            for row in range(table.first_row, table.last_row + 1):
                sheet_cells[(row, index)] = Cell(value=Decimal(1))
        rows, columns = extent.get(table.sheet, (0, 0))
        extent[table.sheet] = (
            max(rows, table.last_row),
            max(columns, len(table.columns)),
        )

    sheets = tuple(
        Sheet(
            name=name,
            max_row=extent[name][0],
            max_column=extent[name][1],
            cells=sheet_cells,
        )
        for name, sheet_cells in cells.items()
    )
    profile = WorkbookProfile(
        sheets=tuple(SheetProfile(name=sheet.name) for sheet in sheets),
        tables=tables,
    )
    return profile, WorkbookData(sheets=sheets)


def _je_type(
    identifier: str,
    variants: tuple[VariantSpec, ...],
    corroborating: tuple[str, ...] = (),
    precedence: int = 10,
) -> JETypeContext:
    return JETypeContext(
        id=identifier,
        name=identifier,
        version=1,
        objective="test",
        areas={
            "je_reconciliation": AreaSpec(weight=100, method=ScoringMethod.WEAKEST_CHECK),
        },
        checks=(
            CheckSpec(
                id="entry_balances",
                area="je_reconciliation",
                question="Does it balance?",
                decided_by=DecidedBy.JUDGEMENT,
            ),
        ),
        discriminators=DiscriminatorSpec(
            precedence=precedence, variants=variants, corroborating=corroborating
        ),
    )


# --- the France shape ---------------------------------------------------------------------

FRANCE = _je_type(
    "vacation-accrual-france",
    variants=(
        VariantSpec(
            id="cp_rtt_france",
            requires_all=("cp_provision", "rtt_provision", "daily_salary_rate"),
            rejects=("adjusted_final_days",),
        ),
    ),
    corroborating=("accounts_present", "fte_classification_column", "reversal_date_populated"),
)

CALCULATION = {
    "A": "Matricule",
    "B": "Total Provision CP",
    "C": "Provision Totale RTT",
    "D": "Daily Salary RTT Rate",
    "E": "FTE / Non FTE",
}
POSTING = {"A": "Header ID", "B": "Account", "C": "Line Amount", "D": "Reversal Date"}


def test_a_workbook_carrying_the_required_columns_is_recognised() -> None:
    profile, data = _workbook(_table("DENT", CALCULATION))

    answer = identify([FRANCE], profile, data)

    assert answer.je_type == "vacation-accrual-france"
    assert answer.variant == "cp_rtt_france"
    assert answer.recognised


def test_the_tab_name_is_never_what_recognises_it() -> None:
    """One sample workbook holds its whole calculation on a tab called DENT."""
    named, data_named = _workbook(_table("GPAY", CALCULATION))
    odd, data_odd = _workbook(_table("DENT", CALCULATION))

    assert (
        identify([FRANCE], named, data_named).je_type == identify([FRANCE], odd, data_odd).je_type
    )


def test_a_workbook_missing_a_required_column_is_not_recognised() -> None:
    without_rtt = {key: value for key, value in CALCULATION.items() if key != "C"}
    profile, data = _workbook(_table("GPAY", without_rtt))

    answer = identify([FRANCE], profile, data)

    assert answer.je_type == UNKNOWN
    assert answer.confidence == 0.0
    assert any("missing rtt_provision" in line for line in answer.rejected)


def test_a_rejecting_column_rules_the_type_out_even_with_every_requirement_met() -> None:
    """``rejects`` is the load-bearing half: a near-miss must not bind."""
    je_type = _je_type(
        "demo",
        variants=(
            VariantSpec(
                id="only",
                requires_all=("cp_provision",),
                rejects=("account",),
            ),
        ),
    )
    profile, data = _workbook(_table("Mixed", {"A": "Total Provision CP", "B": "Account"}))

    answer = identify([je_type], profile, data)

    assert answer.je_type == UNKNOWN
    assert answer.rejected == ("demo/only: rejected by account",)


def test_a_signal_nothing_can_evaluate_fails_closed_and_is_named() -> None:
    """A requirement nobody can check must not be satisfied by nobody checking it."""
    je_type = _je_type(
        "demo",
        variants=(VariantSpec(id="only", requires_all=("cp_provision", "no_such_signal")),),
    )
    profile, data = _workbook(_table("GPAY", CALCULATION))

    answer = identify([je_type], profile, data)

    assert answer.je_type == UNKNOWN
    assert answer.unobservable == ("no_such_signal",)


def test_an_unobservable_signal_in_rejects_does_not_block_a_match() -> None:
    """The other direction of failing closed: it cannot reject what it cannot observe.

    ``adjusted_final_days`` belongs to a type this build does not serve, and the France
    variant rejects on it. It must not keep a France workbook from being recognised.
    """
    profile, data = _workbook(_table("GPAY", CALCULATION))

    answer = identify([FRANCE], profile, data)

    assert answer.recognised
    assert answer.unobservable == ("adjusted_final_days",)


# --- confidence ---------------------------------------------------------------------------


def test_corroboration_from_outside_the_posting_table_raises_the_confidence() -> None:
    bare, bare_data = _workbook(_table("GPAY", {k: v for k, v in CALCULATION.items() if k != "E"}))
    full, full_data = _workbook(_table("GPAY", CALCULATION), _table("JE Upload", POSTING))

    assert identify([FRANCE], bare, bare_data).confidence == pytest.approx(0.6)
    assert identify([FRANCE], full, full_data).confidence == pytest.approx(0.9)


def test_confidence_never_reaches_certainty() -> None:
    """Only a person confirming the type earns 1.0."""
    many = tuple(f"signal_{index}" for index in range(10))
    je_type = _je_type(
        "demo",
        variants=(VariantSpec(id="only", requires_all=("cp_provision",)),),
        corroborating=("accounts_present", *many),
    )
    profile, data = _workbook(
        _table("GPAY", {"A": "Total Provision CP", "B": "Account", "C": "Line Amount"})
    )

    assert identify([je_type], profile, data).confidence <= 0.95


def test_corroboration_found_only_on_the_posting_tab_earns_no_uplift() -> None:
    """The posting tab's own labels are the preparer's claim about their own work.

    The signal is still reported as observed — that is a fact — but it buys no confidence,
    because an identification resting entirely on the posting tab rests on an assertion.
    """
    posting_only, data = _workbook(
        _table("JE Upload", {**POSTING, "E": "Total Provision CP", "F": "FTE / Non FTE"})
    )

    answer = identify(
        [
            _je_type(
                "demo",
                variants=(VariantSpec(id="only", requires_all=("cp_provision",)),),
                corroborating=("fte_classification_column",),
            )
        ],
        posting_only,
        data,
    )

    assert answer.recognised
    assert answer.corroborating == ("fte_classification_column",)
    assert answer.confidence == pytest.approx(0.6), "no uplift from the posting tab alone"


def test_one_signal_from_outside_the_posting_tab_unlocks_the_rest() -> None:
    """A posting-table fact is evidence once something away from the posting tab agrees."""
    profile, data = _workbook(_table("GPAY", CALCULATION), _table("JE Upload", POSTING))

    answer = identify([FRANCE], profile, data)

    assert set(answer.corroborating) == {
        "accounts_present",
        "fte_classification_column",
        "reversal_date_populated",
    }
    assert answer.confidence == pytest.approx(0.9)


def test_a_reversal_date_column_with_nothing_in_it_does_not_corroborate() -> None:
    """A column of reversal dates with no dates is a template, not a reversing accrual."""
    profile, data = _workbook(
        _table("GPAY", CALCULATION),
        _table("JE Upload", POSTING),
        empty=frozenset({("JE Upload", "Reversal Date")}),
    )

    answer = identify([FRANCE], profile, data)

    assert "reversal_date_populated" not in answer.corroborating
    assert answer.confidence == pytest.approx(0.8)


# --- precedence ---------------------------------------------------------------------------


def test_the_more_specific_type_is_tested_first() -> None:
    """Order is a correctness property: a loose shape must not claim a specific workbook."""
    specific = _je_type(
        "specific",
        variants=(VariantSpec(id="tight", requires_all=("cp_provision", "rtt_provision")),),
        precedence=10,
    )
    loose = _je_type(
        "loose",
        variants=(VariantSpec(id="wide", requires_all=("cp_provision",)),),
        precedence=20,
    )
    profile, data = _workbook(_table("GPAY", CALCULATION))

    assert identify([specific, loose], profile, data).je_type == "specific"


def test_no_catalogue_means_no_answer_rather_than_a_default() -> None:
    profile, data = _workbook(_table("GPAY", CALCULATION))

    assert identify([], profile, data).je_type == UNKNOWN
