"""Wire shapes for browsing a recalculation's full row set.

``RecalcRow`` itself is reused directly as the wire shape, the same way ``EntryRecalculation``
is on ``ReviewDetail`` - it already carries nothing that needs hiding or reshaping. These two
exist only to wrap it for paging and for the grouped roll-up.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from review.models.recalculation import RecalcRow


class RecalcRowPage(BaseModel):
    """One page of a recalculation's rows, in whatever order and filter were asked for."""

    model_config = ConfigDict(frozen=True)

    rows: list[RecalcRow]
    total: int = Field(..., description="Rows matching the filter, before paging")
    next_cursor: str | None = Field(default=None, description="Pass back to fetch the next page")


class RecalcGroupSummary(BaseModel):
    """One distinct value of a grouping dimension, rolled up from the rows inside it."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(..., description="The dimension's value, e.g. a cost centre")
    rows: int = Field(..., ge=0)
    flagged: int = Field(..., ge=0, description="Rows in this group carrying a finding")
    recomputed_total: Decimal
    stated_total: Decimal
    difference: Decimal


__all__ = ["RecalcGroupSummary", "RecalcRowPage"]
