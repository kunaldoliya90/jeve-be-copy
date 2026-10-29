"""The review endpoints: upload, progress, result, list.

Four routes, one error shape. The upload is validated *before* the 202 so a file the system
will refuse is refused in the response to the upload, rather than reported thirty seconds
later down a progress stream the client may not even be watching yet.
"""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from api.consts import ApiTag, ErrorCode
from api.exceptions.review_exceptions import UploadRejectedError
from api.pydanticmodels.review_models import (
    AICallCostView,
    AreaView,
    CheckView,
    CostView,
    ErrorResponse,
    EvidenceView,
    ReviewAccepted,
    ReviewDetail,
    ReviewSummary,
    SheetView,
    WorkbookView,
    area_status,
)
from core.apiview import APIView
from core.settings import config
from review.agents.summary import headline_for
from review.context.loader import ContextError, ContextLoader
from review.models.context import OBSERVATION_AREA, CheckSpec, JETypeContext
from review.models.verdicts import CheckOutcome
from review.models.workbook import TableProfile
from review.orchestrator.orchestrator import (
    ReviewOrchestrator,
    ReviewRun,
    document_number_of,
    entry_identity,
    new_review_id,
    not_run_for,
)

logger = logging.getLogger(__name__)

_REJECTION_STATUS = {
    ErrorCode.NOT_EXCEL: status.HTTP_400_BAD_REQUEST,
    ErrorCode.CORRUPT_WORKBOOK: status.HTTP_400_BAD_REQUEST,
    ErrorCode.MACRO_BLOCKED: status.HTTP_400_BAD_REQUEST,
    ErrorCode.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    ErrorCode.AT_CAPACITY: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _orchestrator(request: Request) -> ReviewOrchestrator:
    """The single orchestrator, built once in the lifespan and shared by every request."""
    instance = getattr(request.app.state, "orchestrator", None)
    if instance is None:  # pragma: no cover - a startup failure, not a request-time one
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorResponse(
                error_code=ErrorCode.INTERNAL, message="The review service is not ready"
            ).model_dump(mode="json"),
        )
    assert isinstance(instance, ReviewOrchestrator)  # noqa: S101 - set in the lifespan
    return instance


def _reject(exc: UploadRejectedError) -> HTTPException:
    return HTTPException(
        status_code=_REJECTION_STATUS.get(exc.error_code, status.HTTP_400_BAD_REQUEST),
        detail=ErrorResponse(
            error_code=exc.error_code, message=exc.message, details=exc.details or {}
        ).model_dump(mode="json"),
    )


class ReviewsView(APIView):
    """``POST`` a workbook to start a review; ``GET`` the list of recent ones."""

    @classmethod
    @APIView.configure(
        response_model=ReviewAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=[ApiTag.REVIEWS],
        summary="Upload a journal entry workbook for review",
        responses={
            400: {"model": ErrorResponse, "description": "Not an Excel workbook, or corrupt"},
            413: {"model": ErrorResponse, "description": "Above the upload limit"},
            503: {"model": ErrorResponse, "description": "Too many reviews already running"},
        },
    )
    async def post(
        cls,
        request: Request,
        workbook: Annotated[UploadFile, File(description=".xlsx, .xlsm or .xlsb")],
        je_type: Annotated[str | None, Form()] = None,
    ) -> ReviewAccepted:
        orchestrator = _orchestrator(request)
        if orchestrator.at_capacity():
            raise _reject(
                UploadRejectedError(
                    "Too many reviews are already running; try again shortly",
                    ErrorCode.AT_CAPACITY,
                )
            )

        content = await workbook.read()
        file_name = workbook.filename or "workbook.xlsx"
        review_id = new_review_id()
        try:
            orchestrator.start(review_id, file_name, content, je_type)
        except UploadRejectedError as exc:
            logger.info("upload rejected file=%s code=%s", file_name, exc.error_code)
            raise _reject(exc) from exc

        logger.info(
            "review %s accepted file=%s bytes=%d document=%s",
            review_id,
            file_name,
            len(content),
            document_number_of(file_name) or "-",
        )
        return ReviewAccepted(
            review_id=review_id,
            events_url=f"{config.API_PREFIX}/reviews/{review_id}/events/",
        )

    @classmethod
    @APIView.configure(
        response_model=list[ReviewSummary],
        tags=[ApiTag.REVIEWS],
        summary="List recent reviews, newest first",
    )
    async def get(
        cls,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query()] = None,
    ) -> list[ReviewSummary]:
        store = request.app.state.store
        rows, _ = store.list(limit=limit, cursor=cursor)
        stored = [ReviewSummary.model_validate(row) for row in rows]

        # Reviews still in flight are not in the index yet, and a list that omitted them
        # would look to a user as though their upload had vanished.
        live = [
            ReviewSummary(
                review_id=run.review_id,
                file_name=run.file_name,
                je_type="unknown",
                status=run.status,
                created_at=run.created_at,
            )
            for run in _orchestrator(request).live_runs()
            if run.review_id not in {row["review_id"] for row in rows}
        ]
        return (live + stored)[:limit]


class ReviewView(APIView):
    """One review: its result, or how far a running one has got."""

    @classmethod
    @APIView.configure(
        response_model=ReviewDetail,
        tags=[ApiTag.REVIEWS],
        summary="Fetch one review",
        responses={404: {"model": ErrorResponse, "description": "No such review"}},
    )
    async def get(cls, request: Request, review_id: str) -> ReviewDetail:
        orchestrator = _orchestrator(request)
        run = orchestrator.get(review_id)
        if run is not None and run.reused_from is not None:
            # A reused review never read the workbook, so the live run holds nothing to
            # report. The result it reused is the result, under this upload's own id.
            reused = request.app.state.store.read(run.reused_from)
            if reused is not None:
                return ReviewDetail.from_result(
                    reused, je_spec=_je_spec(request, reused.je_type)
                ).model_copy(
                    update={
                        "review_id": run.review_id,
                        "file_name": run.file_name,
                        "created_at": run.created_at,
                        "reused_from": run.reused_from,
                    }
                )
        if run is not None:
            return _detail(run)

        stored = request.app.state.store.read(review_id)
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error_code=ErrorCode.REVIEW_NOT_FOUND,
                    message=f"No review with id {review_id}",
                    review_id=review_id,
                ).model_dump(mode="json"),
            )
        return ReviewDetail.from_result(stored, je_spec=_je_spec(request, stored.je_type))

    @classmethod
    @APIView.configure(
        status_code=status.HTTP_204_NO_CONTENT,
        tags=[ApiTag.REVIEWS],
        summary="Delete one review",
        responses={
            404: {"model": ErrorResponse, "description": "No such review"},
            409: {"model": ErrorResponse, "description": "Still running; let it finish first"},
        },
    )
    async def delete(cls, request: Request, review_id: str) -> Response:
        """Withdraw a stored review.

        Three things have to go, or the review comes back. The stored record and its index
        rows are the durable copy; the in-memory run is what ``GET`` answers from first and
        what the listing merges in, so a delete that left it behind would remove the review
        from disk and change nothing a user could see.

        A review still in flight is refused rather than deleted. Deleting it would orphan
        the task still writing it and the progress stream still watching it, and step 20
        would then re-create the record that had just been removed. Cancelling a running
        review is a different operation, and this is not it.
        """
        orchestrator = _orchestrator(request)
        run = orchestrator.get(review_id)
        if run is not None and not run.finished:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorResponse(
                    error_code=ErrorCode.REVIEW_RUNNING,
                    message="This review is still running. It can be deleted once it finishes.",
                    review_id=review_id,
                ).model_dump(mode="json"),
            )

        removed = request.app.state.store.delete(review_id)
        # A finished run this worker still holds is a cached answer for `GET`, so the
        # review is only really gone once that has been dropped too.
        orchestrator.forget(review_id)

        if not removed and run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error_code=ErrorCode.REVIEW_NOT_FOUND,
                    message=f"No review with id {review_id}",
                    review_id=review_id,
                ).model_dump(mode="json"),
            )

        logger.info("review %s deleted", review_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReviewEventsView(APIView):
    """Server-Sent Events for one review. The stream closes when the review ends."""

    @classmethod
    @APIView.configure(tags=[ApiTag.REVIEWS], summary="Stream progress for one review")
    async def get(cls, request: Request, review_id: str) -> EventSourceResponse:
        orchestrator = _orchestrator(request)
        run = orchestrator.get(review_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error_code=ErrorCode.REVIEW_NOT_FOUND,
                    message=f"No review with id {review_id}",
                    review_id=review_id,
                ).model_dump(mode="json"),
            )

        async def stream() -> AsyncIterator[dict[str, str]]:
            async for event in orchestrator.subscribe(run):
                yield {"event": event.event.value, "data": event.model_dump_json()}

        # A proxy that buffers would hold every event until the review finished, which is
        # exactly the thing a progress stream exists to avoid.
        return EventSourceResponse(stream(), headers={"X-Accel-Buffering": "no"})


def _je_spec(request: Request, je_type: str) -> JETypeContext | None:
    """The versioned context for a stored review's type, where it is still loaded.

    A type that has since been removed from the catalogue returns None, and the re-read
    then reports each check by its id. That is the honest outcome: the questions belong to
    a version of the business file, and inventing them from the current one would attribute
    wording to a review that never saw it.
    """
    loader = request.app.state.context
    if not isinstance(loader, ContextLoader):  # pragma: no cover - set in the lifespan
        return None
    try:
        return loader.je_type(je_type)
    except ContextError:
        return None


def _detail(run: ReviewRun) -> ReviewDetail:
    """Project a live or just-finished run onto the wire shape."""
    entry = entry_identity(run)
    detail = ReviewDetail(
        review_id=run.review_id,
        status=run.status,
        file_name=run.file_name,
        created_at=run.created_at,
        document_number=entry.document_number,
        risk_rating=entry.risk_rating,
        je_type=entry.je_type,
        je_type_name=entry.je_type_name,
        je_type_variant=entry.je_type_variant,
        je_type_confidence=run.map_verdict.confidence if run.map_verdict else 0.0,
        je_type_corroborated=run.map_verdict.agrees if run.map_verdict else False,
        je_type_source=run.je_type_source,
        process=entry.process,
        sub_process=entry.sub_process,
        period=entry.period,
        step=run.step,
        progress_pct=run.progress_pct,
        cost=CostView(
            ai_calls=run.ai_calls,
            total_tokens=run.ai_tokens,
            total_usd=run.ai_cost,
            duration_ms=run.duration_ms,
            calls=[AICallCostView.of(record) for record in run.call_records],
        ),
        not_yet_run=list(not_run_for(run)),
    )
    if run.error is not None:
        return detail.model_copy(update={"headline": run.error[1]})
    if run.profile is None or run.upload is None:
        return detail

    score = run.score
    if score is not None:
        detail = detail.model_copy(
            update={
                "score": score.total,
                "band": score.band,
                "provisional": score.provisional,
                "areas": _areas(run),
                "observations": _observations(run),
                "needs_person": _needs_person(run),
            }
        )
    if run.context is not None and run.context.evidence_register:
        detail = detail.model_copy(
            update={"evidence": [EvidenceView.of(item) for item in run.context.evidence_register]}
        )
    if run.split_summary is not None:
        # The same object step 20 stored, so the live view and a re-read of the record can
        # never report the split differently.
        detail = detail.model_copy(update={"split_summary": run.split_summary})
    if run.report is not None:
        detail = detail.model_copy(
            update={
                "summary": run.report.summary,
                "next_steps": list(run.report.next_steps),
                "untraced_numbers": list(run.report.untraced_numbers),
            }
        )

    structure = run.context.structure if run.context else None
    calculation = max(
        (table.last_row - table.first_row + 1 for table in run.profile.tables), default=0
    )
    return detail.model_copy(
        update={
            "workbook": WorkbookView(
                file_name=run.file_name,
                size_bytes=run.upload.size_bytes,
                workbook_format=run.upload.workbook_format.value,
                sheet_count=len(run.profile.sheets),
                table_count=len(run.profile.tables),
                row_count=calculation,
                formulas_available=run.data.formulas_available if run.data else True,
                layout_fingerprint=structure.layout_fingerprint if structure else "",
                defect_summary=list(structure.defects_summary) if structure else [],
                sheets=_sheets(run),
                lineage=list(structure.lineage)[:12] if structure else [],
            ),
            "headline": _headline(run),
        }
    )


def _areas(run: ReviewRun) -> list[AreaView]:
    """Each scored area with the checks beneath it, so a score can be taken apart."""
    if run.score is None or run.context is None or run.je_type is None:
        return []

    questions = {check.id: check for check in run.je_type.checks}
    return [
        AreaView(
            area=area.area,
            weight=area.weight,
            method=area.method.value,
            score=area.score,
            status=area_status(
                [outcome for outcome in run.context.outcomes.values() if outcome.area == area.area]
            ),
            reason=area.reason,
            checks=[
                _check_view(outcome, questions.get(outcome.check_id), run)
                for outcome in run.context.outcomes.values()
                if outcome.area == area.area
            ],
        )
        for area in run.score.areas
    ]


def _observations(run: ReviewRun) -> list[CheckView]:
    """Checks that run and are deliberately never scored.

    PII and trend for this entry type: employee data on a calculation tab is expected, so
    scoring it would cap every review, and the trend threshold is unset. They are still
    reported, because a check nobody sees is a check that may as well not have run.
    """
    if run.context is None or run.je_type is None:
        return []
    questions = {check.id: check for check in run.je_type.checks}
    return [
        _check_view(outcome, questions.get(outcome.check_id), run)
        for outcome in run.context.outcomes.values()
        if outcome.area == OBSERVATION_AREA
    ]


def _needs_person(run: ReviewRun) -> list[CheckView]:
    """Everything still waiting on a human, which is what a reviewer opens the report for."""
    if run.context is None or run.je_type is None:
        return []
    questions = {check.id: check for check in run.je_type.checks}
    return [
        _check_view(outcome, questions.get(outcome.check_id), run)
        for outcome in run.context.outcomes.values()
        if outcome.needs_review
    ]


def _check_view(outcome: CheckOutcome, spec: CheckSpec | None, run: ReviewRun) -> CheckView:
    figures = run.context.figures_for(outcome.check_id) if run.context else ()
    decisive = next((figure for figure in figures if figure.decisive), None)
    impact = next((figure.financial_impact for figure in figures if figure.financial_impact), None)
    return CheckView(
        check_id=outcome.check_id,
        area=outcome.area,
        question=spec.question if spec else "",
        status=outcome.final_status,
        decided_by=spec.decided_by.value if spec else "",
        needs_review=outcome.needs_review,
        review_reasons=list(outcome.review_reasons),
        conclusion=decisive.summary if decisive else "",
        exception_count=sum(figure.exception_count for figure in figures),
        financial_impact=impact,
        cells=[
            str(sample.get("cell"))
            for figure in figures
            for sample in figure.samples[:3]
            if sample.get("cell")
        ][:6],
    )


def _sheets(run: ReviewRun) -> list[SheetView]:
    """Every tab the profiler measured, with the first few column labels of its widest table.

    Headers are labels the preparer wrote, not data: they are what makes the structure
    legible to a reviewer, and no cell value is carried with them.
    """
    if run.profile is None:
        return []

    by_sheet: dict[str, list[TableProfile]] = {}
    for table in run.profile.tables:
        by_sheet.setdefault(table.sheet, []).append(table)

    views: list[SheetView] = []
    for sheet in run.profile.sheets:
        tables = by_sheet.get(sheet.name, [])
        widest = max(tables, key=lambda table: len(table.columns), default=None)
        headers = (
            [column.header for column in widest.columns[:6] if column.header] if widest else []
        )
        views.append(
            SheetView(
                name=sheet.name,
                hidden=sheet.hidden,
                row_count=sheet.row_count,
                column_count=sheet.column_count,
                formula_count=sheet.formula_count,
                tables=len(tables),
                headers=headers,
            )
        )
    return views


def _headline(run: ReviewRun) -> str:
    """What a reader sees first. It says what was established, and what was not.

    Once the report exists its headline is the one to use: it was written from the same
    outcomes by ``summary.headline_for`` and is what the stored record carries, so a live
    view and a re-read of the same review never word the result differently.
    """
    if run.report is not None and run.report.headline:
        return run.report.headline
    if run.profile is None:
        return ""
    if run.score is None:
        return (
            f"Profiled {len(run.profile.sheets)} sheets and {len(run.profile.tables)} tables. "
            "No checks have been run, so nothing here is a pass."
        )
    outcomes = run.context.outcomes if run.context else {}
    return headline_for(run.score, outcomes)


__all__ = [
    "AreaView",
    "CheckView",
    "EvidenceView",
    "ReviewEventsView",
    "ReviewView",
    "ReviewsView",
]
