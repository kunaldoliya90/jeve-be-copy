"""`review/tools/entry.py`: the entry-level tools (accounts, period, reversal, evidence, PII).

Only `EvidenceRegisterTool` is covered here for now - the others have no dedicated test file
yet and are exercised indirectly through the integration suite.
"""

from review.models.context import CheckRecipe, CheckSpec, DecidedBy, EvidenceSpec
from review.models.sheet_map import SheetMap, SheetRole
from review.models.verdicts import Status
from review.models.workbook_data import WorkbookData
from review.orchestrator.rules import outcome_for
from review.tools.base import ToolContext
from review.tools.entry import EvidenceRegisterTool
from tests.unit.review.tools.conftest import RULES

REQUIRED_EVIDENCE = (
    EvidenceSpec(id="methodology", required=True, expected="Methodology."),
    EvidenceSpec(id="accrual_calculation", required=True, expected="Working."),
    EvidenceSpec(id="employee_master", required=True, expected="Master."),
    EvidenceSpec(id="approval", required=True, expected="Approval."),
    EvidenceSpec(id="vacation_provision_file", required=False, expected="Provision file."),
    EvidenceSpec(id="provider_screenshot", required=False, expected="Screenshot."),
)


def context(roles: dict[str, SheetRole], evidence: tuple[EvidenceSpec, ...]) -> ToolContext:
    return ToolContext(
        data=WorkbookData(),
        sheet_map=SheetMap(roles=roles),
        rules=RULES,
        je_type_args={"evidence": evidence},
    )


def test_every_required_layer_present_is_a_pass_even_with_an_optional_one_missing() -> None:
    ctx = context(
        {
            "Methodology": SheetRole.METHODOLOGY,
            "GPAY": SheetRole.CALCULATION,
            "MSPeople": SheetRole.MASTER_DATA,
            "Sign-off": SheetRole.APPROVAL,
            # vacation_provision_file (supporting) and provider_screenshot stay missing.
        },
        REQUIRED_EVIDENCE,
    )

    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]

    assert figure.ok is True
    assert figure.coverage is not None
    assert figure.coverage.complete
    assert figure.supports_pass


def test_one_required_layer_missing_is_partial_not_failed_outright() -> None:
    """The case the report showed as a hard 0/100 fail on 4 of 6 layers present."""
    ctx = context(
        {
            "Methodology": SheetRole.METHODOLOGY,
            "GPAY": SheetRole.CALCULATION,
            "MSPeople": SheetRole.MASTER_DATA,
            # "approval" (required) and the two optional layers are missing.
        },
        REQUIRED_EVIDENCE,
    )

    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]

    assert figure.ok is True
    assert figure.coverage is not None
    assert not figure.coverage.complete
    assert figure.coverage.rows_total == 4
    assert figure.coverage.rows_tied == 3
    assert not figure.supports_pass, "partial, not a pass, with a required layer missing"


def test_the_summary_leads_with_the_baseline_share_the_area_is_scored_on() -> None:
    """The sentence the card is written from has to account for the number beside it."""
    ctx = context(
        {
            "Methodology": SheetRole.METHODOLOGY,
            "GPAY": SheetRole.CALCULATION,
            "MSPeople": SheetRole.MASTER_DATA,
        },
        REQUIRED_EVIDENCE,
    )

    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]

    assert figure.summary.startswith("3 of 4 baseline evidence layers are present")
    assert "(3 of 6 expected layers in all)" in figure.summary
    assert "approval" in figure.summary


def test_no_required_layer_present_fails_outright() -> None:
    ctx = context({}, REQUIRED_EVIDENCE)

    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]

    assert figure.ok is False


def test_with_no_required_layer_declared_it_falls_back_to_failing_on_any_gap() -> None:
    """A JE type that declares no required evidence at all keeps the old all-or-nothing read,
    rather than reporting a coverage that has nothing in it to be partial about."""
    ctx = context({"Methodology": SheetRole.METHODOLOGY}, evidence=())

    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]

    assert figure.coverage is None
    assert figure.ok is False


def test_the_check_reaches_partial_through_the_ordinary_figures_routing() -> None:
    """Step 16's own `outcome_for`, not a special case for this check."""
    ctx = context(
        {"Methodology": SheetRole.METHODOLOGY, "GPAY": SheetRole.CALCULATION},
        REQUIRED_EVIDENCE,
    )
    figure = EvidenceRegisterTool().run(ctx, "evidence_baseline")[0]
    check = CheckSpec(
        id="evidence_baseline",
        area="evidence_completeness",
        question="Are the required evidence items present?",
        decided_by=DecidedBy.FIGURES,
        recipe=CheckRecipe(tool="evidence_register"),
    )

    outcome = outcome_for(check, (figure,))

    assert outcome.final_status is Status.PARTIAL
    assert outcome.needs_review
