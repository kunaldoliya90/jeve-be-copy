"""Browsing a recalculation beyond the work paper's own capped sample.

The work paper on ``GET /reviews/{id}/`` prints the worst twelve records, which is the right
size for a page that is also printed. These two routes exist for the different question a
reviewer asks next: not "what is wrong", but "show me everything, grouped my way." Both read
straight off the stored row file - see ``review/storage/reviews.py`` - so a review still
running answers them the moment step 13c has written it, the same as a finished one.
"""

from typing import Annotated

from fastapi import HTTPException, Query, Request, status

from api.consts import ApiTag, ErrorCode
from api.pydanticmodels.recalc_models import RecalcGroupSummary, RecalcRowPage
from api.pydanticmodels.review_models import ErrorResponse
from core.apiview import APIView
from review.models.recalculation import RecalcRow
from review.recalc.rows_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    filter_rows,
    group_rows,
    paginate,
    sort_rows,
)
from review.storage.reviews import ReviewStore


def _rows_or_404(request: Request, review_id: str) -> list[RecalcRow]:
    store: ReviewStore = request.app.state.store
    rows = store.read_recalc_rows(review_id)
    if rows is not None:
        return rows

    # Distinguish "no such review" from "this review never recalculated anything" - a
    # 404 either way, but the message a reviewer gets back should say which.
    if store.read(review_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                error_code=ErrorCode.REVIEW_NOT_FOUND,
                message=f"No review with id {review_id}",
                review_id=review_id,
            ).model_dump(mode="json"),
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorResponse(
            error_code=ErrorCode.REVIEW_NOT_FOUND,
            message="This review has no recalculation to browse",
            review_id=review_id,
        ).model_dump(mode="json"),
    )


class RecalculationRowsView(APIView):
    """Every record a recalculation walked, paged, filtered and sorted."""

    @classmethod
    @APIView.configure(
        response_model=RecalcRowPage,
        tags=[ApiTag.REVIEWS],
        summary="Page through a recalculation's full row set",
        responses={404: {"model": ErrorResponse, "description": "No such review, or no rows"}},
    )
    async def get(
        cls,
        request: Request,
        review_id: str,
        status_: Annotated[
            str | None,
            Query(
                alias="status",
                description="'all' (default), 'changed', 'agrees', or one DiscrepancyKind value",
            ),
        ] = None,
        sort: Annotated[
            str | None, Query(description="A field name, '-' prefixed for descending")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        cursor: Annotated[str | None, Query()] = None,
    ) -> RecalcRowPage:
        rows = _rows_or_404(request, review_id)
        filtered = sort_rows(filter_rows(rows, status=status_), sort=sort)
        page = paginate(filtered, limit=limit, cursor=cursor)
        return RecalcRowPage(rows=list(page.rows), total=page.total, next_cursor=page.next_cursor)


class RecalculationGroupsView(APIView):
    """A recalculation's rows, rolled up by one grouping dimension."""

    @classmethod
    @APIView.configure(
        response_model=list[RecalcGroupSummary],
        tags=[ApiTag.REVIEWS],
        summary="Roll a recalculation's rows up by one dimension",
        responses={404: {"model": ErrorResponse, "description": "No such review, or no rows"}},
    )
    async def get(
        cls,
        request: Request,
        review_id: str,
        group_by: Annotated[str, Query(description="A grouping dimension, e.g. 'cost_center'")],
        status_: Annotated[str | None, Query(alias="status")] = None,
    ) -> list[RecalcGroupSummary]:
        rows = _rows_or_404(request, review_id)
        filtered = filter_rows(rows, status=status_)
        return [
            RecalcGroupSummary(
                value=summary.value,
                rows=summary.rows,
                flagged=summary.flagged,
                recomputed_total=summary.recomputed_total,
                stated_total=summary.stated_total,
                difference=summary.difference,
            )
            for summary in group_rows(filtered, dimension=group_by)
        ]


__all__ = ["RecalculationGroupsView", "RecalculationRowsView"]
