"""What a review says when it could not check anything.

The property these pin down is the one the screenshot of a blank report made obvious: a
workbook nothing recognises must still produce something a person can act on. Every
assertion below is about *naming* — which candidate was ruled out, which table could not be
bound, which header meant nothing — and never about claiming a result.
"""

from review.context.binder import BindResult
from review.context.identify import Identification
from review.models.sheet_map import SheetMap
from review.models.workbook import SheetProfile, WorkbookProfile
from review.orchestrator.diagnosis import diagnose

CATALOGUE = ("vacation-accrual-france",)

PROFILE = WorkbookProfile(
    sheets=(SheetProfile(name="Sheet1", row_count=30, column_count=4),),
    tables=(),
)

RULED_OUT = Identification(
    rejected=(
        "vacation-accrual-france/cp_rtt_france: missing cp_provision, rtt_provision",
        "vacation-accrual-france/ms_vacation_global: missing adjusted_final_days",
    ),
    unobservable=("adjusted_final_days",),
)


def _binding(
    *,
    bound: tuple[str, ...] = (),
    unbound: tuple[str, ...] = ("calculation", "je_posting"),
    unresolved: tuple[str, ...] = (),
    identification: Identification | None = RULED_OUT,
) -> BindResult:
    return BindResult(
        sheet_map=SheetMap(),
        bound=bound,
        unbound_tables=unbound,
        unresolved_headers=unresolved,
        identification=identification,
    )


def test_diagnose_names_every_candidate_the_shape_ruled_out() -> None:
    report = diagnose(_binding(), PROFILE, catalogue=CATALOGUE)

    assert any("cp_rtt_france" in step and "cp_provision" in step for step in report.next_steps)
    assert any("ms_vacation_global" in step for step in report.next_steps)


def test_diagnose_names_the_columns_that_would_have_bound_a_missing_table() -> None:
    """A reviewer cannot act on 'calculation is unbound'; they can act on which columns."""
    report = diagnose(_binding(), PROFILE, catalogue=CATALOGUE)

    calculation = next(step for step in report.next_steps if "'calculation'" in step)
    assert "employee_ref" in calculation
    assert "'matricule'" in calculation, "the spellings it already knows, as examples"


def test_diagnose_lists_the_headers_that_matched_no_meaning() -> None:
    report = diagnose(
        _binding(unresolved=("Jours Acquis", "Taux Horaire")), PROFILE, catalogue=CATALOGUE
    )

    assert any("'Jours Acquis'" in step and "'Taux Horaire'" in step for step in report.next_steps)
    assert any("different names" in step for step in report.next_steps)


def test_diagnose_tells_an_uploader_who_named_no_type_to_name_one() -> None:
    report = diagnose(_binding(), PROFILE, declared=None, catalogue=CATALOGUE)

    advice = next(step for step in report.next_steps if "Upload again" in step)
    assert "vacation-accrual-france" in advice
    assert "unconfirmed" in advice, "and that it will not read as a clean pass"


def test_diagnose_states_the_obstacle_before_the_instruction() -> None:
    """A reader told to add a header spelling has to know which header went unrecognised."""
    report = diagnose(
        _binding(unresolved=("Jours Acquis",)), PROFILE, declared=None, catalogue=CATALOGUE
    )

    findings = next(i for i, step in enumerate(report.next_steps) if "'Jours Acquis'" in step)
    instructions = next(i for i, step in enumerate(report.next_steps) if "different names" in step)
    assert findings < instructions


def test_diagnose_says_so_when_the_declared_type_is_not_in_the_catalogue() -> None:
    report = diagnose(_binding(), PROFILE, declared="made-up-type", catalogue=CATALOGUE)

    assert any("'made-up-type'" in step for step in report.next_steps)


def test_diagnose_never_reports_an_unrun_check_as_a_pass() -> None:
    """The one sentence that must be in every one of these reports."""
    report = diagnose(_binding(), PROFILE, catalogue=CATALOGUE)

    assert "No check ran" in report.headline
    assert "not the same as a check that passed" in report.summary


def test_diagnose_blames_the_calculation_table_when_that_is_what_is_missing() -> None:
    report = diagnose(_binding(unbound=("calculation",), bound=("je_posting",)), PROFILE)

    assert "calculation table could not be found" in report.headline
    assert any("header row is a single row of labels" in step for step in report.next_steps)


def test_diagnose_reports_a_recognised_type_that_could_not_be_mapped() -> None:
    """Recognised and unusable is a different problem from unrecognised, and reads so."""
    identified = Identification(je_type="vacation-accrual-france", variant="cp_rtt_france")
    report = diagnose(
        _binding(unbound=("calculation",), identification=identified),
        PROFILE,
        catalogue=CATALOGUE,
    )

    assert "calculation table could not be found" in report.headline
    assert "Nothing in the catalogue matched" not in report.summary
