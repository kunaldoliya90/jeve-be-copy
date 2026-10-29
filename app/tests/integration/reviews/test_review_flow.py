"""The review endpoints, end to end through the app, on a real workbook.

These run the actual pipeline: gatekeeper, reader, profiler, structure context. Nothing is
mocked, because the thing worth testing is that the contract a browser codes against matches
what the harness actually produces.
"""

import json
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.consts import ErrorCode, SSEEvent
from core.settings import config
from review.guardrails.map_check import CORROBORATED_CONFIDENCE, DISPUTED_CONFIDENCE
from tests.conftest import orchestrator_of, store_of
from tests.doubles import StubLLMProvider

WORKBOOKS = Path(__file__).resolve().parents[4] / "skills" / "docs"
REVIEWS = "/api/jeve/reviews/"


@pytest.fixture
def unfamiliar_workbook() -> bytes:
    """A workbook the binder can place but no discriminator recognises.

    Three columns the alias table knows, which is enough to claim the calculation table and
    nowhere near enough to prove what entry this is. That gap — bindable but unidentified —
    is what the declared type exists to cover.
    """
    book = Workbook()
    sheet = book.create_sheet("Accrual Detail", index=0)
    sheet.append(["Matricule", "Total Accrual", "FTE Vacation Accrual"])
    for row in range(1, 25):
        sheet.append([f"E{row:04d}", Decimal(100 + row), Decimal(100 + row)])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def unreadable_workbook() -> bytes:
    """A real workbook of something else entirely: nothing here binds to anything."""
    book = Workbook()
    sheet = book.create_sheet("Notes", index=0)
    sheet.append(["Comment", "Author"])
    sheet.append(["Looks fine to me", "A reviewer"])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def workbook() -> bytes:
    path = WORKBOOKS / "236264.xlsx"
    if not path.is_file():
        pytest.skip(f"Sample workbook not present at {path}")
    return path.read_bytes()


@pytest.fixture
def api(client: TestClient) -> TestClient:
    """The real app. ``isolated_environment`` already chdirs to a temp directory, so the
    store's relative root lands there and no test can see another test's records."""
    return client


def _upload(api: TestClient, name: str, content: bytes) -> Any:
    return api.post(
        REVIEWS,
        files={"workbook": (name, content, "application/vnd.ms-excel")},
        data={"je_type": "vacation-accrual-france"},
    )


# ------------------------------------------------------------------------------ upload


def test_a_real_workbook_is_accepted(api: TestClient, workbook: bytes) -> None:
    response = _upload(api, "236264.xlsx", workbook)
    assert response.status_code == 202
    body = response.json()
    assert body["review_id"].startswith("rev_")
    assert body["status"] == "queued"
    assert body["events_url"].endswith("/events/")


def test_a_pdf_named_xlsx_is_refused_on_its_content(api: TestClient) -> None:
    """The refusal arrives in the upload response, not thirty seconds later down a stream."""
    response = _upload(api, "accrual.xlsx", b"%PDF-1.7 not a workbook at all")
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.NOT_EXCEL


def test_an_unsupported_format_is_refused(api: TestClient) -> None:
    response = _upload(api, "accrual.csv", b"a,b,c\n1,2,3\n")
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == ErrorCode.NOT_EXCEL


def test_an_empty_upload_is_refused(api: TestClient) -> None:
    response = _upload(api, "accrual.xlsx", b"")
    assert response.status_code == 400


# ---------------------------------------------------------------------------- the result


def test_the_review_completes_and_reports_what_it_found(api: TestClient, workbook: bytes) -> None:
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]

    # Draining the event stream also waits for the background task to finish.
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        events = [line for line in stream.iter_lines() if line.startswith("event:")]
    assert events, "a progress stream that yields nothing is useless to a UI"

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    assert detail["status"] in {"completed", "needs_review"}
    assert detail["progress_pct"] == 100

    book = detail["workbook"]
    assert book["sheet_count"] == 10
    assert book["table_count"] >= 7
    assert book["workbook_format"] == ".xlsx"
    # The full digest, not the shortened display form: the API returns the identifier and
    # the UI decides how much of it to show.
    assert len(book["layout_fingerprint"]) == 64


def test_the_result_names_what_it_did_not_do(api: TestClient, workbook: bytes) -> None:
    """The guarantee that makes a partial build safe to demonstrate.

    A score is now computed from the checks that are decided by arithmetic. The phases that
    do not run are still named, and because every judgement check reports as unproven, the
    score must come back provisional — a reader must never mistake an absent judgement for
    a passed one.
    """
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    assert detail["not_yet_run"], "phases that did not run must be named"
    assert detail["score"] is not None, "the arithmetic checks produce a score"
    assert detail["band"]
    assert detail["provisional"] is True, "unproven checks must keep the score provisional"
    assert detail["status"] == "needs_review"


def test_the_score_is_taken_apart_into_areas(api: TestClient, workbook: bytes) -> None:
    """A score nobody can decompose is a number to be argued with, not evidence."""
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    areas = detail["areas"]
    assert len(areas) == 8, "every weighted area is reported, scored or not"
    assert sum(area["weight"] for area in areas) == 100
    assert all(0 <= area["score"] <= 100 for area in areas)
    assert detail["needs_person"], "unproven checks are listed for a reviewer"


def test_the_accrual_reperforms_row_by_row(api: TestClient, workbook: bytes) -> None:
    """The check the whole review exists for, on a real file.

    2,280 employee rows is the figure the re-performance engine publishes for this
    workbook, so agreement here is agreement with an independently verified number.
    """
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    checks = {check["check_id"]: check for area in detail["areas"] for check in area["checks"]}
    accrual = checks["accrual_reperforms"]
    assert accrual["status"] == "pass"
    assert "2,280 of 2,280 rows agree" in accrual["conclusion"]


def test_the_recalculation_work_paper_reaches_the_api(api: TestClient, workbook: bytes) -> None:
    """The arithmetic a reviewer reads, on the real file, through the real contract.

    Every figure asserted here was produced by code walking cells and nothing else, so these
    are the numbers 236264.xlsx actually holds. ``27,839,758`` is not in this test by
    accident: it is the FTE vacation accrual the workbook states above its own data, and the
    point of the section is that an independent re-add of 2,280 employee rows reaches it.
    """
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    paper = api.get(f"{REVIEWS}{review_id}/").json()["split_summary"]
    assert paper is not None, "a workbook that bound its calculation tab must produce one"

    basis = paper["basis"]
    assert basis["sheet"] == "GPAY"
    assert basis["rows_read"] == 2_280, "the employee rows, without the subtotal beneath them"
    assert basis["classification_meaning"] == "fte_classification"

    # Discovered from the column, not configured anywhere.
    assert {entry["label"] for entry in paper["categories"]} == {"FTE", "Non-FTE"}

    # Each component's classes add up to its own total, which is what makes the matrix
    # readable as a matrix rather than as a list of unrelated figures.
    for component in paper["components"]:
        assert Decimal(component["total"]) == sum(
            (Decimal(amount) for amount in component["amounts"].values()), Decimal(0)
        ), component["header"]

    # And every total the preparer typed above a column agrees with the re-add beneath it.
    assert paper["tie_outs"]
    assert all(entry["within_tolerance"] for entry in paper["tie_outs"])


def test_the_work_paper_carries_no_employee_identifier(api: TestClient, workbook: bytes) -> None:
    """The traced rows are row numbers. This file's employee names must not leave it.

    Asserted over the serialised work paper rather than over the model, because the thing
    that matters is what crosses the network. "Confidential" is what the preparer redacted
    the name column to, so finding it would mean that column had been shown at all.

    Scoped to the work paper on purpose. The response as a whole does carry "Matricule", on
    ``workbook.sheets[].headers`` - deliberately, and for a different reason: a header is a
    label the preparer wrote rather than anybody's data, and it is what makes the structure
    legible to a reviewer. This section is the one that shows *values*, so it is the one that
    has to leave the identifying columns out.
    """
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    paper = api.get(f"{REVIEWS}{review_id}/").json()["split_summary"]
    body = json.dumps(paper)
    assert "Confidential" not in body
    assert "Matricule" not in body


def test_the_work_paper_survives_a_re_read_of_the_stored_record(
    api: TestClient, workbook: bytes
) -> None:
    """It is part of the work product, so it has to come back after the run is forgotten.

    The figures a check cited are working memory and deliberately are not kept; this is not,
    because its arithmetic *is* the evidence. A re-read that lost it would leave a stored
    review less defensible than the same review was an hour earlier.
    """
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    live = api.get(f"{REVIEWS}{review_id}/").json()["split_summary"]
    orchestrator_of(api).forget(review_id)
    stored = api.get(f"{REVIEWS}{review_id}/").json()["split_summary"]

    assert stored == live


def test_a_workbook_that_binds_nothing_gets_no_work_paper(
    api: TestClient, unreadable_workbook: bytes
) -> None:
    """``null``, not an empty one.

    A section of dashes is indistinguishable from a section that failed to compute, and this
    review already says what it could not do in terms a person can act on.
    """
    review_id = _upload(api, "notes.xlsx", unreadable_workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    assert api.get(f"{REVIEWS}{review_id}/").json()["split_summary"] is None


def test_progress_events_carry_no_cell_contents(api: TestClient, workbook: bytes) -> None:
    """Events say where the system looked. The workbook's contents do not cross the wire."""
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]

    payloads = []
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        for line in stream.iter_lines():
            if line.startswith("data:"):
                payloads.append(json.loads(line.removeprefix("data:").strip()))

    assert payloads
    assert {payload["event"] for payload in payloads} <= set(SSEEvent)
    for payload in payloads:
        for value in payload["detail"].values():
            assert not isinstance(value, str) or "Confidential" not in value


def test_an_unknown_review_is_a_404(api: TestClient) -> None:
    response = api.get(f"{REVIEWS}rev_does_not_exist/")
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == ErrorCode.REVIEW_NOT_FOUND


def test_events_for_an_unknown_review_are_a_404(api: TestClient) -> None:
    assert api.get(f"{REVIEWS}rev_nope/events/").status_code == 404


# ------------------------------------------------------------------------------ listing


def test_a_finished_review_can_be_deleted(api: TestClient, workbook: bytes) -> None:
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        stream.read()

    assert api.delete(f"{REVIEWS}{review_id}/").status_code == 204
    assert api.get(f"{REVIEWS}{review_id}/").status_code == 404


def test_a_deleted_review_leaves_the_list(api: TestClient, workbook: bytes) -> None:
    """The listing merges the store with the runs this worker still holds, so a delete that
    only cleared the store would remove the review from disk and change nothing visible."""
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        stream.read()

    api.delete(f"{REVIEWS}{review_id}/")

    listed = [row["review_id"] for row in api.get(REVIEWS).json()]
    assert review_id not in listed


def test_a_deleted_review_is_reviewed_again_rather_than_reused(
    api: TestClient, workbook: bytes
) -> None:
    """The reuse index must lose the entry too. Left behind, the next identical upload is
    answered from a review that no longer exists."""
    first = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{first}/events/") as stream:
        stream.read()
    api.delete(f"{REVIEWS}{first}/")

    second = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{second}/events/") as stream:
        stream.read()

    detail = api.get(f"{REVIEWS}{second}/").json()
    assert detail["reused_from"] is None
    assert detail["status"] in {"completed", "needs_review"}


def test_deleting_an_unknown_review_is_a_404(api: TestClient) -> None:
    response = api.delete(f"{REVIEWS}rev_nope/")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == ErrorCode.REVIEW_NOT_FOUND


def test_a_running_review_cannot_be_deleted(api: TestClient, workbook: bytes) -> None:
    """Deleting one mid-flight would orphan the task still writing it, and step 20 would
    then re-create the record that had just been removed. Cancelling is a different thing."""
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]

    response = api.delete(f"{REVIEWS}{review_id}/")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == ErrorCode.REVIEW_RUNNING
    # And it really is still there.
    assert api.get(f"{REVIEWS}{review_id}/").status_code == 200


def test_a_running_review_appears_in_the_list(api: TestClient, workbook: bytes) -> None:
    """A listing built only from stored records would omit an upload still in flight, and
    to the person who just made it that reads as the upload having vanished."""
    review_id = _upload(api, "236264.xlsx", workbook).json()["review_id"]
    rows = api.get(REVIEWS).json()
    assert review_id in {row["review_id"] for row in rows}


def test_the_list_is_empty_before_anything_is_uploaded(api: TestClient) -> None:
    assert api.get(REVIEWS).json() == []


# ------------------------------------------------------------------- the written report


def _finish(api: TestClient, workbook: bytes) -> str:
    """Upload and drain the stream, which is also how a test waits for the background task."""
    review_id = str(_upload(api, "236264.xlsx", workbook).json()["review_id"])
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())
    return review_id


def test_the_report_is_written_even_with_no_model_configured(
    api: TestClient, workbook: bytes
) -> None:
    """Step 18 falls back to a template rather than leaving the report blank.

    This is the deterministic path, and it is the one a demonstration without a key runs,
    so the summary it produces has to stand on its own.
    """
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["headline"].startswith(f"{detail['score']} out of 100")
    assert "checks ran on this entry" in detail["summary"]
    assert detail["next_steps"], "a template still says what a person has to do"
    assert detail["untraced_numbers"] == [], "a template quotes only figures it was given"
    assert detail["cost"]["total_usd"] == "0", "no model ran, so nothing was spent"


def test_every_number_in_the_template_summary_traces(api: TestClient, workbook: bytes) -> None:
    """Step 19 over step 18's own output, on a real workbook: the check that must never
    fire on a summary code assembled."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["untraced_numbers"] == []


def _agrees_on_the_type(stub: StubLLMProvider) -> None:
    """Make the stub corroborate the type, so a test is not also testing a dispute.

    The citation is on the calculation tab on purpose: it has to sit outside the posting
    table to count as evidence rather than as the preparer's own label.
    """
    stub.answers["understanding"] = {
        "je_type": "vacation-accrual-france",
        "confidence": 0.9,
        "citations": [{"sheet": "GPAY", "cell": "A1", "why": "employee-level CP and RTT"}],
        "roles": [],
    }


def test_the_model_writes_the_report_when_one_is_configured(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    _agrees_on_the_type(stub_model)
    stub_model.answers["summary"] = {
        "summary": (
            "The entry reperformed in full. Population could not be validated: the employee "
            "reference is redacted, so the population cannot be tied."
        ),
        "next_steps": ["Obtain an unredacted or hashed employee reference."],
    }

    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    assert detail["summary"].startswith("The entry reperformed in full")
    assert "the population cannot be tied" in detail["summary"]
    assert detail["next_steps"] == ["Obtain an unredacted or hashed employee reference."]
    # The headline stays code's: the score and the counts are not left to prose.
    assert detail["headline"].startswith(f"{detail['score']} out of 100")


# ------------------------------------------------------------- the type, read twice


def test_the_type_is_recognised_from_the_shape_with_no_model(
    api: TestClient, workbook: bytes
) -> None:
    """Tier 2 settles a known workbook on its own, and says so in the record."""
    stored = store_of(api).read(_finish(api, workbook))

    assert stored is not None
    assert stored.je_type == "vacation-accrual-france"
    # 0.9: the variant matched and all three corroborating signals were found outside the
    # posting table. Never 1.0 without a person.
    assert stored.audit.je_type_confidence == 0.9
    assert stored.audit.je_type_corroborated is True


def test_a_second_reading_that_agrees_raises_the_recorded_confidence(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    _agrees_on_the_type(stub_model)

    stored = store_of(stub_client).read(_finish(stub_client, workbook))

    assert stored is not None
    assert stored.audit.je_type_corroborated is True
    assert stored.audit.je_type_confidence == CORROBORATED_CONFIDENCE


def test_a_second_reading_that_cannot_place_the_file_sends_it_to_a_person(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """The point of the step. The checks still run on code's binding; the review is flagged.

    The stub answers ``{}`` for an unset call, which the schema reads as ``unknown`` — the
    honest answer from a reader that could not place the file, and the one the harness has
    to handle without either ignoring it or acting on it.
    """
    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    assert detail["status"] == "needs_review"
    assert "could not place it" in detail["summary"]
    assert detail["score"] is not None, "a disputed type does not stop the arithmetic"

    stored = store_of(stub_client).read(detail["review_id"])
    assert stored is not None
    assert stored.audit.je_type_corroborated is False
    assert stored.audit.je_type_confidence == DISPUTED_CONFIDENCE


def test_a_citation_pointing_at_an_empty_cell_is_reported_unverified(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    stub_model.answers["understanding"] = {
        "je_type": "vacation-accrual-france",
        "confidence": 0.9,
        "citations": [
            {"sheet": "GPAY", "cell": "A1", "why": "the calculation"},
            {"sheet": "GPAY", "cell": "ZZ99999", "why": "nothing is here"},
            {"sheet": "No Such Tab", "cell": "A1", "why": "nor here"},
        ],
        "roles": [],
    }

    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    assert "point at cells that are empty or absent" in detail["summary"]


def test_the_type_reading_never_sees_the_workbook(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """AI-1 gets shape and labels. Column totals cross; employee values never do."""
    _finish(stub_client, workbook)

    payload = stub_model.payload("understanding")
    assert set(payload) == {
        "journal_entry_types_this_system_knows",
        "sheet_roles_you_may_assign",
        "workbook",
    }
    # It is not told which type the preparer selected, nor what the shape rules concluded.
    rendered = json.dumps(payload)
    assert "Confidential" not in rendered
    for sheet in payload["workbook"]["sheets"]:
        assert set(sheet) == {
            "name",
            "hidden",
            "row_count",
            "headers",
            "column_types",
            "column_totals",
            "category_samples",
        }


def test_a_number_the_model_invented_is_named_not_removed(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """The guardrail that matters most, because the sentence survives.

    Deleting the figure would leave prose reading as though it had been verified; naming it
    tells the reader which number to distrust.
    """
    stub_model.answers["summary"] = {
        "summary": "The accrual understates the liability by 918273.45.",
        "next_steps": [],
    }

    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    assert detail["untraced_numbers"] == ["918273.45"]
    assert "918273.45" in detail["summary"]


def test_the_report_never_shows_the_model_the_workbook(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """The boundary, asserted on what actually crossed it rather than on the prompt."""
    _finish(stub_client, workbook)

    payload = stub_model.payload("summary")
    assert set(payload) == {
        "journal_entry_type",
        "review_notes",
        "score",
        "checks",
        "largest_findings",
        "exceptions_grouped_by_cause",
        "personal_data_flagged",
    }
    assert "Confidential" not in json.dumps(payload), "no employee reference crosses the boundary"


# ----------------------------------------------------------------------------- the record


def test_the_finished_review_is_stored_and_reads_back_the_same(
    api: TestClient, workbook: bytes
) -> None:
    """Step 20. A work product that exists only in a worker's memory is not a work product."""
    review_id = _finish(api, workbook)
    live = api.get(f"{REVIEWS}{review_id}/").json()

    stored = store_of(api).read(review_id)
    assert stored is not None, "the review must survive the process that produced it"
    assert stored.score is not None
    assert stored.score.total == live["score"]
    assert stored.report is not None
    assert stored.report.headline == live["headline"]
    assert stored.report.summary == live["summary"]
    # Every check the live view showed is in the record, observations included: a stored
    # result that dropped a check would read as a cleaner review than the one that ran.
    on_the_wire = {check["check_id"] for area in live["areas"] for check in area["checks"]}
    on_the_wire |= {check["check_id"] for check in live["needs_person"]}
    assert on_the_wire <= {check.check_id for check in stored.checks}


def test_the_stored_record_carries_the_trail_that_explains_it(
    api: TestClient, workbook: bytes
) -> None:
    stored = store_of(api).read(_finish(api, workbook))

    assert stored is not None
    audit = stored.audit
    assert len(audit.file_sha256) == 64
    assert audit.file_name == "236264.xlsx"
    assert audit.size_bytes == len(workbook)
    assert len(audit.layout_fingerprint) == 64
    assert audit.versions["rules"], "the rules version is what makes a result replayable"
    assert audit.reuse_hit is False
    assert audit.flags, "the reasons a check went to a person are kept with the record"


def test_a_stored_review_is_served_after_the_run_is_forgotten(
    api: TestClient, workbook: bytes
) -> None:
    """The reason persistence exists: the record outlives the worker that produced it."""
    review_id = _finish(api, workbook)
    live = api.get(f"{REVIEWS}{review_id}/").json()

    # Simulate a restart: the in-memory run is gone, the record is not.
    orchestrator_of(api).forget(review_id)

    reread = api.get(f"{REVIEWS}{review_id}/").json()
    assert reread["status"] == live["status"]
    assert reread["score"] == live["score"]
    assert reread["headline"] == live["headline"]
    assert reread["summary"] == live["summary"]
    assert reread["next_steps"] == live["next_steps"]
    assert reread["progress_pct"] == 100


def test_a_stored_review_appears_in_the_list_after_a_restart(
    api: TestClient, workbook: bytes
) -> None:
    review_id = _finish(api, workbook)
    orchestrator_of(api).forget(review_id)

    rows = api.get(REVIEWS).json()
    row = next(row for row in rows if row["review_id"] == review_id)
    assert row["file_name"] == "236264.xlsx"
    assert row["status"] == "needs_review"
    assert row["score"] is not None


def test_the_same_file_under_the_same_rules_is_reused(api: TestClient, workbook: bytes) -> None:
    """Step 3 against step 20: the reuse index is only useful once something is stored."""
    _finish(api, workbook)
    second = _finish(api, workbook)

    detail = api.get(f"{REVIEWS}{second}/").json()
    assert detail["progress_pct"] == 100
    assert detail["status"] in {"completed", "needs_review"}


# -------------------------------------------------------------- a layout already proved out


def _finish_as(api: TestClient, workbook: bytes, je_type: str | None) -> str:
    """Upload under a given submitted type, so the reuse key differs run to run.

    The reuse index keys on the file digest *and* the type the uploader named, so this is
    how the same bytes can be made to run twice in full — which is what exercises the
    layout cache rather than the reuse short-circuit in front of it.
    """
    response = api.post(
        REVIEWS,
        files={"workbook": ("236264.xlsx", workbook, "application/vnd.ms-excel")},
        data={"je_type": je_type} if je_type else {},
    )
    review_id = str(response.json()["review_id"])
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())
    return review_id


def test_a_known_layout_is_not_read_by_a_model_a_second_time(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """Step 7. The saving that matters is the model calls, not the arithmetic.

    The arithmetic runs in full both times — it is cheap, deterministic and the thing the
    review is for. What a known layout skips is re-deriving a map and plan that were already
    proved out under these same prompts.
    """
    _agrees_on_the_type(stub_model)

    first = _finish_as(stub_client, workbook, "vacation-accrual-france")
    reads_after_first = sum(1 for r in stub_model.requests if r.schema_name == "understanding")

    second = _finish_as(stub_client, workbook, None)
    reads_after_second = sum(1 for r in stub_model.requests if r.schema_name == "understanding")

    assert first != second
    assert reads_after_first == 1
    assert reads_after_second == 1, "the second run must not pay to read the layout again"

    stored = store_of(stub_client).read(second)
    assert stored is not None
    assert stored.audit.layout_hit is True
    assert stored.score is not None


def test_the_second_run_scores_the_same_as_the_first(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """A cache that changed an answer would be worse than no cache at all."""
    _agrees_on_the_type(stub_model)

    first = stub_client.get(
        f"{REVIEWS}{_finish_as(stub_client, workbook, 'vacation-accrual-france')}/"
    ).json()
    second = stub_client.get(f"{REVIEWS}{_finish_as(stub_client, workbook, None)}/").json()

    assert second["score"] == first["score"]
    assert second["band"] == first["band"]
    assert [area["score"] for area in second["areas"]] == [area["score"] for area in first["areas"]]


def test_a_layout_whose_map_had_a_note_is_not_remembered(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """A map nobody confirmed must not be spread across every file from that template.

    With the stub left unset, AI-1 cannot place the file, step 9 records that, and the
    layout is therefore not cached — so the second run reads it again.
    """
    _finish_as(stub_client, workbook, "vacation-accrual-france")
    second = _finish_as(stub_client, workbook, None)

    reads = sum(1 for r in stub_model.requests if r.schema_name == "understanding")
    assert reads == 2, "a doubtful map is re-read rather than reused"

    stored = store_of(stub_client).read(second)
    assert stored is not None
    assert stored.audit.layout_hit is False


# ------------------------------------------------------------------------- the plan


def test_the_plan_is_built_from_the_recipes_and_every_step_survives_checking(
    api: TestClient, workbook: bytes
) -> None:
    """Step 12 on a real workbook: no step is dropped, so no check is left without evidence."""
    review_id = _finish_as(api, workbook, "vacation-accrual-france")
    detail = api.get(f"{REVIEWS}{review_id}/").json()

    assert "could not be checked" not in detail["summary"]

    stored = store_of(api).read(review_id)
    assert stored is not None
    # Every check the context file states produced an outcome, which is only true if every
    # planned step ran.
    assert len(stored.checks) == 18


# ---------------------------------------------------------------- what the review cost


def test_a_review_that_called_no_model_reports_no_cost(api: TestClient, workbook: bytes) -> None:
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["cost"]["ai_calls"] == 0
    assert detail["cost"]["total_usd"] == "0"
    assert detail["cost"]["duration_ms"] > 0


def test_a_review_that_called_models_reports_what_it_spent(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """A live run reported $0 for a review that had made three paid calls."""
    _agrees_on_the_type(stub_model)

    review_id = _finish(stub_client, workbook)
    detail = stub_client.get(f"{REVIEWS}{review_id}/").json()

    assert detail["cost"]["ai_calls"] >= 3, "AI-1, AI-3 and AI-5 at least"
    assert detail["cost"]["total_tokens"] > 0
    assert Decimal(detail["cost"]["total_usd"]) > 0


def test_the_cost_survives_a_restart_with_the_record(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    _agrees_on_the_type(stub_model)
    review_id = _finish(stub_client, workbook)
    live = stub_client.get(f"{REVIEWS}{review_id}/").json()

    orchestrator_of(stub_client).forget(review_id)
    reread = stub_client.get(f"{REVIEWS}{review_id}/").json()

    assert reread["cost"]["ai_calls"] == live["cost"]["ai_calls"]
    assert reread["cost"]["total_usd"] == live["cost"]["total_usd"]


def test_the_variant_the_shape_settled_on_is_reported(api: TestClient, workbook: bytes) -> None:
    """A reviewer has to know which accrual model the checks were run against."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["je_type"] == "vacation-accrual-france"
    assert detail["je_type_variant"] == "cp_rtt_france"


def test_the_lifespan_passes_every_review_limit_into_the_orchestrator(api: TestClient) -> None:
    """A setting that nothing reads is a setting that lies. These are the six that exist."""
    orchestrator = orchestrator_of(api)

    assert orchestrator._max_active == config.MAX_ACTIVE_REVIEWS
    assert orchestrator._deadline_s == config.REVIEW_DEADLINE_S
    assert orchestrator._max_upload_bytes == config.MAX_UPLOAD_BYTES
    assert orchestrator._tool_workers == config.TOOL_WORKERS
    assert orchestrator._structure_max_tokens == config.STRUCTURE_CONTEXT_MAX_TOKENS
    assert orchestrator._figure_samples == config.FIGURE_SAMPLES


# --------------------------------------------------------------- the entry, and what it cost


def test_the_header_names_the_type_the_process_and_the_period(
    api: TestClient, workbook: bytes
) -> None:
    """What a reviewer reads before anything else, and all of it worked out by the system."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["je_type"] == "vacation-accrual-france"
    assert detail["je_type_name"] == "France vacation accrual"
    assert detail["je_type_variant"] == "cp_rtt_france"
    assert detail["process"] == "Record to Report"
    assert detail["sub_process"] == "Payroll"
    assert detail["je_type_confidence"] == 0.9
    assert detail["je_type_corroborated"] is True
    assert detail["period"], "the fiscal period is read off the posting table by code"


def test_the_document_number_is_the_name_the_workbook_arrived_under(
    api: TestClient, workbook: bytes
) -> None:
    """Nobody types it any more, so it is on every review rather than on almost none."""
    review_id = api.post(
        REVIEWS,
        files={"workbook": ("236264.xlsx", workbook, "application/vnd.ms-excel")},
        data={"je_type": "vacation-accrual-france"},
    ).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    assert api.get(f"{REVIEWS}{review_id}/").json()["document_number"] == "236264"


def test_the_risk_rating_is_the_band_the_score_reached(api: TestClient, workbook: bytes) -> None:
    """One fact in two shapes. They are not two opinions, and must never disagree."""
    review_id = api.post(
        REVIEWS,
        files={"workbook": ("236264.xlsx", workbook, "application/vnd.ms-excel")},
        data={"je_type": "vacation-accrual-france"},
    ).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    bands = {"High compliance": "low", "Medium risk": "medium", "High risk": "high"}
    assert detail["risk_rating"] == bands[detail["band"]]


def test_the_upload_ignores_a_document_number_or_rating_sent_with_it(
    api: TestClient, workbook: bytes
) -> None:
    """Posting them is not an error - an older client should not start failing - but
    neither value reaches the review, and the one it reports is its own."""
    review_id = api.post(
        REVIEWS,
        files={"workbook": ("236264.xlsx", workbook, "application/vnd.ms-excel")},
        data={
            "je_type": "vacation-accrual-france",
            "document_number": "100122-7",
            "risk_rating": "catastrophic",
        },
    ).json()["review_id"]
    with api.stream("GET", f"{REVIEWS}{review_id}/events/") as stream:
        list(stream.iter_lines())

    detail = api.get(f"{REVIEWS}{review_id}/").json()
    assert detail["document_number"] == "236264"
    assert detail["risk_rating"] != "catastrophic"


def test_the_evidence_register_reports_every_expected_layer(
    api: TestClient, workbook: bytes
) -> None:
    """Six layers in the business file, six rows in the register. A register that listed
    only the gaps would hide what the conclusion actually rests on."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    register = detail["evidence"]
    assert len(register) == 6
    assert all(row["expected"] for row in register), "every row says what was expected"
    assert all(row["note"] for row in register), "and why it reads as it does"
    assert {row["status"] for row in register} <= {"available", "missing"}
    assert any(row["required"] for row in register)


def test_the_tested_parameters_carry_a_status_as_well_as_a_score(
    api: TestClient, workbook: bytes
) -> None:
    """An area is no better than its worst check, whatever the weighted arithmetic says."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    areas = detail["areas"]
    assert all(area["status"] for area in areas)
    assert {area["status"] for area in areas} <= {
        "pass",
        "partial",
        "unable_to_validate",
        "missing_evidence",
        "fail",
    }


def test_unscored_observations_are_reported_separately(api: TestClient, workbook: bytes) -> None:
    """PII and trend run and are deliberately never scored. A check nobody sees may as
    well not have run, so they are reported alongside the weighted areas, not inside them."""
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    observations = detail["observations"]
    assert observations, "the observation checks must reach the reader"
    assert all(check["area"] == "observation" for check in observations)
    scored = {check["check_id"] for area in detail["areas"] for check in area["checks"]}
    assert not scored & {check["check_id"] for check in observations}


def test_a_deterministic_run_reports_no_model_calls_at_all(
    api: TestClient, workbook: bytes
) -> None:
    detail = api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()

    assert detail["cost"]["calls"] == []
    assert detail["cost"]["total_usd"] == "0"


def test_every_model_call_is_charged_to_the_step_that_made_it(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """A single total says what a review cost. The per-step breakdown says what to change."""
    _agrees_on_the_type(stub_model)
    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    calls = detail["cost"]["calls"]
    assert calls, "a run with a model configured must report what each call cost"
    assert len(calls) == detail["cost"]["ai_calls"]
    assert all(call["step"] for call in calls), "every call names the step that made it"
    assert all(call["name"] for call in calls), "and what that step was doing"
    assert {call["step"] for call in calls} <= {"8", "11", "13a", "13b", "14", "15", "18"}
    assert sum(call["total_tokens"] for call in calls) == detail["cost"]["total_tokens"]
    assert sum(Decimal(call["cost_usd"]) for call in calls) == Decimal(detail["cost"]["total_usd"])


def test_each_call_is_charged_to_its_own_numbered_step(
    stub_client: TestClient, stub_model: StubLLMProvider, workbook: bytes
) -> None:
    """The mapping that matters: two calls on one step must not be merged, and the two
    readers that fire between step 12 and step 13 belong to 13a, not to whichever step
    happened to be current when they ran."""
    _agrees_on_the_type(stub_model)
    detail = stub_client.get(f"{REVIEWS}{_finish(stub_client, workbook)}/").json()

    steps = {call["call"]: call["step"] for call in detail["cost"]["calls"]}
    assert steps["AI-1"] == "8"
    assert steps["AI-M"] == "13a"
    assert steps["AI-R"] == "13a"
    assert steps["AI-3"] == "14"
    assert steps["AI-5"] == "18"
    # Two calls, one step, still two rows: a reader has to see that 13a reads the
    # methodology and the screenshots separately.
    assert len([call for call in detail["cost"]["calls"] if call["step"] == "13a"]) == 2


def test_a_re_read_of_a_stored_review_still_carries_the_register_and_the_cost(
    api: TestClient, workbook: bytes
) -> None:
    """The run is dropped from memory after fifty finished reviews, and the browser's next
    refresh is served from the record. A page that empties out on a refresh is a page
    nobody can rely on in a review meeting."""
    review_id = _finish(api, workbook)
    orchestrator_of(api).forget(review_id)

    detail = api.get(f"{REVIEWS}{review_id}/").json()

    assert detail["score"] is not None
    assert len(detail["areas"]) == 8
    assert sum(area["weight"] for area in detail["areas"]) == 100
    assert len(detail["evidence"]) == 6
    assert detail["je_type_name"] == "France vacation accrual"
    assert detail["process"] == "Record to Report"
    assert detail["period"]
    assert detail["observations"]
    assert detail["file_name"] == "236264.xlsx"
    # Restored from the versioned context file, not from the record: the questions belong
    # to a version of the business file and are never reconstructed from run state.
    assert all(check["question"] for area in detail["areas"] for check in area["checks"]), (
        "a re-read still says what each check asked"
    )


def test_a_re_read_does_not_invent_the_conclusions_it_no_longer_holds(
    api: TestClient, workbook: bytes
) -> None:
    """Figures are working memory and are not stored. The status survives a re-read; the
    sentence that explained it does not, and a plausible replacement would be a fiction."""
    review_id = _finish(api, workbook)
    live = api.get(f"{REVIEWS}{review_id}/").json()
    orchestrator_of(api).forget(review_id)
    stored = api.get(f"{REVIEWS}{review_id}/").json()

    def statuses(detail: Any) -> dict[str, str]:
        return {
            check["check_id"]: check["status"]
            for area in detail["areas"]
            for check in area["checks"]
        }

    assert statuses(stored) == statuses(live), "the verdicts are unchanged by a re-read"
    assert all(check["conclusion"] == "" for area in stored["areas"] for check in area["checks"])


# ------------------------------------------------- a workbook the shape does not recognise


def test_an_unrecognised_workbook_still_says_what_was_missing(
    api: TestClient, unreadable_workbook: bytes
) -> None:
    """The blank-report failure: no type, no checks, and nothing a reviewer could do next."""
    review_id = _finish_as(api, unreadable_workbook, None)
    detail = api.get(f"{REVIEWS}{review_id}/").json()

    assert detail["status"] == "needs_review"
    assert detail["je_type_source"] == "unknown"
    assert "No check ran" in detail["headline"]
    steps = detail["next_steps"]
    assert steps, "a review nobody can act on is the bug being fixed here"
    assert any("cp_rtt_france" in step for step in steps), "which type was ruled out"
    assert any("'calculation'" in step for step in steps), "and which table was missing"
    assert detail["score"] is None, "nothing ran, so nothing is scored"


def test_an_unrecognised_workbook_is_stored_like_any_other_review(
    api: TestClient, unreadable_workbook: bytes
) -> None:
    """It is a work product: a review that vanishes on a restart was never a review."""
    review_id = _finish_as(api, unreadable_workbook, None)
    orchestrator_of(api).forget(review_id)

    reread = api.get(f"{REVIEWS}{review_id}/").json()

    assert reread["status"] == "needs_review"
    assert reread["next_steps"], "the advice survives the restart with the record"
    assert review_id in [row["review_id"] for row in api.get(REVIEWS).json()]


def test_the_declared_type_runs_the_checks_when_the_shape_settles_nothing(
    api: TestClient, unfamiliar_workbook: bytes
) -> None:
    """A type the discriminators have not been taught must not mean nobody checks the file."""
    review_id = _finish(api, unfamiliar_workbook)
    detail = api.get(f"{REVIEWS}{review_id}/").json()

    assert detail["je_type"] == "vacation-accrual-france", "what the upload declared"
    assert detail["je_type_source"] == "declared"
    assert detail["je_type_confidence"] == 0.0, "declared is never evidence"
    assert detail["score"] is not None, "the checks ran and the arithmetic scored them"
    assert sum(len(area["checks"]) for area in detail["areas"]) > 0


def test_a_declared_type_never_reports_as_completed(
    api: TestClient, unfamiliar_workbook: bytes
) -> None:
    """Nobody established what this entry is, so nobody can sign it off."""
    review_id = _finish(api, unfamiliar_workbook)
    detail = api.get(f"{REVIEWS}{review_id}/").json()

    assert detail["status"] == "needs_review"
    assert any("did not establish" in step for step in detail["next_steps"])
    assert "matched no entry type" in detail["summary"]


def test_a_declared_type_is_never_remembered_as_a_known_layout(
    api: TestClient, unfamiliar_workbook: bytes
) -> None:
    """Caching it would spread one upload's assumption over every file of that shape.

    The cache holds maps nobody had to question. A map that exists only because somebody
    named a type on an upload form is the opposite of that.
    """
    _finish(api, unfamiliar_workbook)

    assert len(orchestrator_of(api)._layouts) == 0


# ------------------------------------------------------------ the same file, uploaded twice


def test_a_reused_review_answers_with_the_result_it_reused(
    api: TestClient, workbook: bytes
) -> None:
    """The blank page a second upload used to show: no type, no score, no report, 0.0s.

    The run behind a reuse hit deliberately never reads the workbook, so answering from it
    produced an empty result for a file that had in fact been reviewed in full.
    """
    first = _finish(api, workbook)
    original = api.get(f"{REVIEWS}{first}/").json()

    second = _finish(api, workbook)
    reused = api.get(f"{REVIEWS}{second}/").json()

    assert reused["reused_from"] == first
    assert reused["review_id"] == second, "served under this upload's own id"
    assert reused["je_type"] == original["je_type"] != "unknown"
    assert reused["score"] == original["score"]
    assert len(reused["areas"]) == len(original["areas"]) > 0
    assert reused["headline"] == original["headline"]


def test_a_review_that_is_not_a_reuse_says_so(api: TestClient, workbook: bytes) -> None:
    assert api.get(f"{REVIEWS}{_finish(api, workbook)}/").json()["reused_from"] is None
