"""What the orchestrator assembles for a reader: the evidence register, the period, the entry.

None of these decide anything. They turn what the checks already established into the three
things a reviewer opens the report to see first — which entry this is, what evidence stood
behind it, and which period it belongs to.
"""

from decimal import Decimal

import pytest

from api.consts import RiskRating
from review.models.context import (
    AreaSpec,
    CheckSpec,
    DecidedBy,
    EvidenceSpec,
    JETypeContext,
)
from review.models.evidence import EvidenceStatus, Figure
from review.models.result import Score, ScoringMethod
from review.orchestrator.orchestrator import (
    ReviewRun,
    _evidence_register,
    _period_from,
    document_number_of,
    entry_identity,
)
from review.tools.entry import EvidenceRegisterTool, PeriodAgreementTool

JE_TYPE = JETypeContext(
    id="vacation-accrual-france",
    name="France vacation accrual",
    version=2,
    process="Record to Report",
    sub_process="Payroll",
    objective="test",
    areas={"evidence_completeness": AreaSpec(weight=100, method=ScoringMethod.BASELINE_SHARE)},
    checks=(
        CheckSpec(
            id="evidence_present",
            area="evidence_completeness",
            question="Is every expected layer attached?",
            decided_by=DecidedBy.JUDGEMENT,
        ),
    ),
    evidence=(
        EvidenceSpec(
            id="employee_master",
            required=True,
            expected="Employee population, status, and FTE data.",
            remediation="Attach the employee master extract.",
        ),
        EvidenceSpec(
            id="approval",
            required=True,
            expected="Approval for the leave accrual.",
            remediation="Record the approver and the approval date.",
        ),
        EvidenceSpec(
            id="vacation_provision_file",
            required=False,
            expected="Vacation provision file supporting this journal entry.",
        ),
    ),
)


def _register_figure(samples: list[dict[str, object]]) -> Figure:
    return Figure(
        figure_id="F-01",
        check_id="evidence_present",
        tool=EvidenceRegisterTool.name,
        ok=False,
        samples=tuple(samples),
    )


# ------------------------------------------------------------------- the evidence register


def test_the_register_reports_every_expected_layer_not_only_the_gaps() -> None:
    """A register showing only what is missing hides what the conclusion actually rests on."""
    register = _evidence_register(
        {
            "evidence_present": (
                _register_figure(
                    [
                        {"item": "approval", "present": False},
                        {"item": "employee_master", "present": True, "location": "MSPeople"},
                        {"item": "vacation_provision_file", "present": False},
                    ]
                ),
            )
        },
        JE_TYPE,
    )

    assert [item.item_id for item in register] == [
        "employee_master",
        "approval",
        "vacation_provision_file",
    ], "the register follows the business file's order, not the tool's"
    assert register[0].status is EvidenceStatus.AVAILABLE
    assert register[1].status is EvidenceStatus.MISSING


def test_a_satisfied_layer_names_the_tab_that_satisfied_it() -> None:
    register = _evidence_register(
        {
            "evidence_present": (
                _register_figure(
                    [{"item": "employee_master", "present": True, "location": "MSPeople"}]
                ),
            )
        },
        JE_TYPE,
    )

    found = register[0]
    assert found.location == "MSPeople"
    assert found.note == "Satisfied by MSPeople."
    assert found.expected == "Employee population, status, and FTE data."


def test_a_layer_the_tool_flagged_carries_the_reason_rather_than_just_missing() -> None:
    """Four outcomes a register can be in, and 'missing' alone sends a reviewer hunting
    for a file that is already pasted into the workbook."""
    register = _evidence_register(
        {
            "evidence_present": (
                _register_figure(
                    [
                        {
                            "item": "approval",
                            "present": False,
                            "issue": "a screenshot is present but was not read",
                        }
                    ]
                ),
            )
        },
        JE_TYPE,
    )

    approval = next(item for item in register if item.item_id == "approval")
    assert approval.note == "Not available: a screenshot is present but was not read."
    assert approval.remediation == "Record the approver and the approval date."


def test_a_layer_no_check_examined_is_missing_and_says_so() -> None:
    """Fails closed. A layer nobody looked for must never read as one nobody needed."""
    register = _evidence_register({}, JE_TYPE)

    assert all(item.status is EvidenceStatus.MISSING for item in register)
    assert register[0].note == "No check examined employee master, so nothing was established."


def test_the_register_keeps_required_and_recommended_apart() -> None:
    register = _evidence_register({}, JE_TYPE)

    assert [item.required for item in register] == [True, True, False]


# -------------------------------------------------------------------------------- the period


def _period_figure(samples: tuple[dict[str, object], ...]) -> dict[str, tuple[Figure, ...]]:
    return {
        "period_agrees": (
            Figure(
                figure_id="F-02",
                check_id="period_agrees",
                tool=PeriodAgreementTool.name,
                ok=True,
                samples=samples,
                numbers=(Decimal(len(samples)),),
            ),
        )
    }


def test_the_period_comes_from_the_check_that_already_read_it() -> None:
    """Code's own reading, so the header and the period check can never disagree."""
    pack = _period_figure(
        (
            {"period": "08", "fiscal_year": "2026", "lines": 12},
            {"period": "07", "fiscal_year": "2026", "lines": 1},
        )
    )

    assert _period_from(pack) == "2026 P08", "the most common label, which the tool orders first"


def test_a_period_with_no_single_year_is_reported_bare_rather_than_guessed() -> None:
    """Two fiscal years on one entry is itself the finding. Picking one would hide it."""
    pack = _period_figure(({"period": "08", "fiscal_year": "", "lines": 12},))

    assert _period_from(pack) == "08"


def test_no_period_is_reported_when_nothing_read_one() -> None:
    assert _period_from({}) is None


# ------------------------------------------------------------------------- the entry header


def test_the_entry_reports_what_the_system_established_about_it() -> None:
    run = ReviewRun(review_id="rev_1", file_name="236264.xlsx", period="FY26P07")
    run.je_type = JE_TYPE

    entry = entry_identity(run)

    assert entry.document_number == "236264"
    assert entry.je_type_name == "France vacation accrual"
    assert (entry.process, entry.sub_process) == ("Record to Report", "Payroll")
    assert entry.period == "FY26P07"


def test_the_risk_rating_is_absent_until_the_review_has_been_scored() -> None:
    """It is the band the score reached, and before step 17 there is no score to band.
    Reporting "low" on a review nothing has scored would be an invented all-clear."""
    run = ReviewRun(review_id="rev_1", file_name="236264.xlsx")

    assert entry_identity(run).risk_rating is None

    run.score = Score(total=32.5, band="High risk", risk_rating=RiskRating.HIGH)

    assert entry_identity(run).risk_rating == RiskRating.HIGH


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("236264.xlsx", "236264"),
        ("305800.xlsm", "305800"),
        ("JE 100122-7 final.xlsb", "JE 100122-7 final"),
        # A browser on Windows can send a path rather than a bare name. None of it is a
        # directory to the person looking at the file, so no segment is thrown away.
        (r"C:\uploads\236264.xlsx", r"C:\uploads\236264"),
        ("no-suffix", "no-suffix"),
        (".xlsx", None),
        ("", None),
    ],
)
def test_the_document_number_is_the_file_name_without_its_suffix(
    file_name: str, expected: str | None
) -> None:
    assert document_number_of(file_name) == expected


def test_an_entry_whose_type_was_never_recognised_claims_nothing() -> None:
    """A header that fills a type in from a default is a header that has lied once."""
    entry = entry_identity(ReviewRun(review_id="rev_2", file_name="unknown.xlsx"))

    assert entry.je_type == "unknown"
    assert entry.je_type_name == ""
    assert entry.process == ""
    assert entry.period is None
