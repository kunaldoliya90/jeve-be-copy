"""Step 9: whether AI-1's reading survives being checked against the workbook."""

from decimal import Decimal

from review.agents.understand import Understanding
from review.context.identify import Identification
from review.guardrails.map_check import (
    CORROBORATED_CONFIDENCE,
    DISPUTED_CONFIDENCE,
    map_check,
)
from review.models.sheet_map import SheetMap, SheetRole
from review.models.workbook_data import Cell, Sheet, WorkbookData

FRANCE = "vacation-accrual-france"


def _data() -> WorkbookData:
    """Two sheets: a calculation tab and a posting tab, each with one populated cell."""
    return WorkbookData(
        sheets=(
            Sheet(
                name="GPAY",
                max_row=2,
                max_column=2,
                cells={(1, 1): Cell(value="Matricule"), (2, 2): Cell(value=Decimal(10))},
            ),
            Sheet(
                name="JE Upload",
                max_row=2,
                max_column=1,
                cells={(1, 1): Cell(value="Header ID")},
            ),
        )
    )


def _sheet_map() -> SheetMap:
    return SheetMap(
        roles={"GPAY": SheetRole.CALCULATION, "JE Upload": SheetRole.JE_POSTING},
        je_type=FRANCE,
    )


def _recognised(je_type: str = FRANCE, confidence: float = 0.9) -> Identification:
    return Identification(je_type=je_type, variant="cp_rtt_france", confidence=confidence)


def _understanding(je_type: str = FRANCE, **cites: str) -> Understanding:
    return Understanding(
        je_type=je_type,
        confidence=0.9,
        citations=tuple(
            {"sheet": sheet.replace("_", " "), "cell": cell, "why": "evidence"}
            for sheet, cell in cites.items()
        ),
    )


# --- no second reading --------------------------------------------------------------------


def test_with_no_second_reading_the_shape_stands_and_the_report_says_so() -> None:
    """An uncorroborated answer is not a wrong answer, and it is not a corroborated one."""
    verdict = map_check(None, _recognised(), _sheet_map(), _data())

    assert verdict.agrees
    assert verdict.confidence == 0.9
    assert "no second reading corroborated it" in verdict.notes[0]


# --- agreement ----------------------------------------------------------------------------


def test_agreement_cited_from_outside_the_posting_tab_raises_the_confidence() -> None:
    verdict = map_check(_understanding(GPAY="B2"), _recognised(), _sheet_map(), _data())

    assert verdict.agrees
    assert verdict.confidence == CORROBORATED_CONFIDENCE
    assert verdict.citations[0].sheet == "GPAY"
    assert verdict.notes == ()


def test_agreement_cited_only_from_the_posting_tab_earns_nothing() -> None:
    """The posting tab's labels are the preparer's claim about their own work."""
    verdict = map_check(_understanding(JE_Upload="A1"), _recognised(), _sheet_map(), _data())

    assert verdict.agrees, "agreement is still agreement"
    assert verdict.confidence == 0.9, "but it buys no extra confidence"
    assert "cited only the posting tab" in verdict.notes[0]


def test_a_citation_at_an_empty_cell_is_reported_and_dropped() -> None:
    verdict = map_check(
        _understanding(GPAY="B2", JE_Upload="Z99"), _recognised(), _sheet_map(), _data()
    )

    assert verdict.agrees
    assert [citation.cell for citation in verdict.citations] == ["B2"]
    assert "point at cells that are empty or absent" in verdict.notes[0]
    assert "JE Upload!Z99" in verdict.notes[0]


def test_a_citation_on_a_sheet_that_does_not_exist_is_reported() -> None:
    understanding = Understanding(
        je_type=FRANCE,
        citations=({"sheet": "No Such Tab", "cell": "A1", "why": "nothing"},),
    )

    verdict = map_check(understanding, _recognised(), _sheet_map(), _data())

    assert verdict.citations == ()
    assert "No Such Tab!A1" in verdict.notes[0]


def test_a_reference_that_is_not_an_a1_address_is_reported_not_crashed_on() -> None:
    understanding = Understanding(
        je_type=FRANCE, citations=({"sheet": "GPAY", "cell": "not a cell", "why": ""},)
    )

    verdict = map_check(understanding, _recognised(), _sheet_map(), _data())

    assert verdict.citations == ()
    assert verdict.notes


# --- disagreement -------------------------------------------------------------------------


def test_a_reading_that_names_a_different_type_disputes_the_review() -> None:
    verdict = map_check(
        _understanding(je_type="some-other-type", GPAY="B2"),
        _recognised(),
        _sheet_map(),
        _data(),
    )

    assert verdict.disputed
    assert verdict.confidence == DISPUTED_CONFIDENCE
    assert "disagree on what this entry is" in verdict.notes[-1]
    assert "some-other-type" in verdict.notes[-1]


def test_a_reading_that_cannot_place_the_file_disputes_it_too() -> None:
    """Half an answer is not agreement. A person is asked to confirm the type."""
    verdict = map_check(
        _understanding(je_type="unknown", GPAY="B2"), _recognised(), _sheet_map(), _data()
    )

    assert verdict.disputed
    assert "could not place it" in verdict.notes[-1]


def test_a_dispute_names_the_type_the_checks_actually_ran_against() -> None:
    """The reader has to know which methodology produced the score they are looking at."""
    verdict = map_check(
        _understanding(je_type="unknown", GPAY="B2"), _recognised(), _sheet_map(), _data()
    )

    assert FRANCE in verdict.notes[-1]


def test_agreement_on_a_type_the_shape_never_recognised_is_not_agreement() -> None:
    """Both saying "unknown" is two failures, not a corroborated answer."""
    verdict = map_check(
        _understanding(je_type="unknown", GPAY="B2"),
        Identification(),
        _sheet_map(),
        _data(),
    )

    assert verdict.disputed
