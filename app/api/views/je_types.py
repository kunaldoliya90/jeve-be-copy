"""What a journal entry of a given type is checked on.

This is the review *plan*, readable before any review runs: the scored areas and their
weights, every check and who decides it, and the evidence the business expects. It comes
straight from the versioned context files, so the screen and the harness can never disagree
about what will be tested.
"""

import logging

from fastapi import HTTPException, Request, status

from api.consts import ApiTag, ErrorCode
from api.pydanticmodels.review_models import (
    AreaSpecView,
    CheckSpecView,
    ErrorResponse,
    EvidenceView,
    JETypeView,
)
from core.apiview import APIView
from review.context.loader import ContextError, ContextLoader
from review.models.context import OBSERVATION_AREA, CheckSpec, JETypeContext

logger = logging.getLogger(__name__)


def _context(request: Request) -> ContextLoader:
    loader = request.app.state.context
    assert isinstance(loader, ContextLoader)  # noqa: S101 - built in the lifespan
    return loader


def _check(spec: CheckSpec) -> CheckSpecView:
    return CheckSpecView(
        check_id=spec.id,
        area=spec.area,
        question=spec.question,
        decided_by=spec.decided_by.value,
        risk=spec.risk,
        tool=spec.recipe.tool if spec.recipe else None,
    )


def _view(context: JETypeContext) -> JETypeView:
    return JETypeView(
        id=context.id,
        name=context.name,
        version=context.version,
        process=context.process,
        sub_process=context.sub_process,
        objective=context.objective,
        aliases=list(context.aliases),
        areas=[
            AreaSpecView(
                area=area,
                weight=spec.weight,
                method=spec.method.value,
                checks=[_check(check) for check in context.checks_for(area)],
            )
            for area, spec in context.areas.items()
        ],
        observations=[_check(check) for check in context.checks_for(OBSERVATION_AREA)],
        evidence=[
            EvidenceView(
                item=item.id,
                expected=item.expected,
                required=item.required,
                status="expected",
                location=None,
                remediation=item.remediation or None,
            )
            for item in context.evidence
        ],
        check_count=len(context.checks),
        figures_decided=sum(1 for check in context.checks if check.decided_by == "figures"),
    )


class JETypesView(APIView):
    """Every journal entry type the service knows."""

    @classmethod
    @APIView.configure(
        response_model=list[JETypeView],
        tags=[ApiTag.JE_TYPES],
        summary="List the journal entry types and what each one is checked on",
    )
    async def get(cls, request: Request) -> list[JETypeView]:
        loader = _context(request)
        return [_view(loader.je_type(entry["id"])) for entry in loader.catalogue]


class JETypeDetailView(APIView):
    """One journal entry type."""

    @classmethod
    @APIView.configure(
        response_model=JETypeView,
        tags=[ApiTag.JE_TYPES],
        summary="Fetch one journal entry type",
        responses={404: {"model": ErrorResponse, "description": "No such type"}},
    )
    async def get(cls, request: Request, je_type_id: str) -> JETypeView:
        try:
            return _view(_context(request).je_type(je_type_id))
        except ContextError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse(
                    error_code=ErrorCode.REVIEW_NOT_FOUND, message=exc.message
                ).model_dump(mode="json"),
            ) from exc
