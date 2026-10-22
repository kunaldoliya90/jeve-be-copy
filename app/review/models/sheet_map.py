"""Where everything sits in this particular workbook: AI-1's answer, verified at step 9.

``SheetRole`` carries vocabulary for both JE types the harness serves. A tie-out entry needs
a posting sheet and a system source; a re-performance entry needs a calculation sheet, the
aggregation sheets between it and the posting lines, and the master data used to validate it.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from review.models.workbook import CellRef, TableRef


class SheetRole(StrEnum):
    """What a sheet is for. Decided from the columns it holds, never from its name."""

    JE_POSTING = "je_posting"
    SYSTEM_SOURCE = "system_source"
    CALCULATION = "calculation"
    """Employee-level working, e.g. GPAY. Where re-performance happens."""
    AGGREGATION = "aggregation"
    """Groups the calculation sheet toward the posting lines, e.g. a pivot."""
    JE_STAGING = "je_staging"
    """Turns aggregated amounts into journal entry lines before the posting sheet."""
    MASTER_DATA = "master_data"
    """Reference data a check validates against, e.g. employee or entity master."""
    SELF_CHECK = "self_check"
    """The workbook's own balance or reasonableness tab. Re-derived, never trusted."""
    SUPPORTING = "supporting"
    CALENDAR = "calendar"
    BALANCE_CHECK = "balance_check"
    METHODOLOGY = "methodology"
    APPROVAL = "approval"
    IGNORE = "ignore"


class ChainHop(BaseModel):
    """One roll-up step, e.g. calculation -> aggregation, grouped by some keys.

    The hops are validated by re-summing them, not by trusting the claim: a grand total that
    agrees while two hops cancel out is exactly what hop-by-hop checking catches.
    """

    model_config = ConfigDict(frozen=True)

    from_sheet: str = Field(..., min_length=1)
    to_sheet: str = Field(..., min_length=1)
    group_by: tuple[str, ...] = Field(
        default=(), description='Column meanings the hop groups on, e.g. ("company_code",)'
    )
    amount_column: str = Field(..., min_length=1, description="Meaning of the column summed")


class CalculationChain(BaseModel):
    """The ordered path from the employee-level working to the posted entry."""

    model_config = ConfigDict(frozen=True)

    hops: tuple[ChainHop, ...] = Field(default=())


class SheetMap(BaseModel):
    """AI-1's output. Nothing here is believed until ``map_check`` has verified it.

    Fact *cells* are named here; their *values* are read by code at step 10, never taken
    from the model.
    """

    model_config = ConfigDict(frozen=True)

    roles: dict[str, SheetRole] = Field(default_factory=dict, description="Sheet name -> role")
    tables: dict[str, TableRef] = Field(
        default_factory=dict, description="Table name from the JE type -> where it is"
    )
    facts: dict[str, CellRef] = Field(
        default_factory=dict, description="Fact name -> the cell holding it"
    )
    evidence: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, description="Evidence item id -> sheet names or picture ids"
    )
    name_cells: tuple[CellRef, ...] = Field(
        default=(), description="Cells holding people's names, for the PII scan"
    )
    chain: CalculationChain | None = Field(
        default=None, description="Roll-up path, for JE types that re-perform"
    )
    je_type: str = Field(default="unknown", description="Blind classification, or 'unknown'")
    je_type_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    je_type_citations: tuple[CellRef, ...] = Field(
        default=(),
        description="Proof of the type. At least one must sit outside the posting table",
    )
