"""Step 7a and 8, deterministically: what each sheet is, and which column means what.

This is tier 2 of the identification design. A sheet is recognised by **the column families
it carries**, never by its name — one sample workbook holds its entire calculation on a tab
called ``DENT``, where no name-based rule would ever look. Columns are then bound by alias,
so the same mapping works on two workbooks whose header rows differ by one and whose column
letters do not line up.

Nothing here calls a model. A workbook it cannot bind is reported unbound, which routes the
affected checks to a person — far better than a plausible binding to the wrong arithmetic.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from review.context.aliases import resolve_meaning
from review.context.identify import Identification, identify
from review.models.context import JETypeContext
from review.models.sheet_map import SheetMap, SheetRole
from review.models.workbook import TableProfile, WorkbookProfile
from review.models.workbook_data import WorkbookData
from review.utils.sheet_utils import normalize_header

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RoleRule:
    """What a table must carry to be a given role, and what rules it out.

    ``rejects`` is the load-bearing half. Without it a near-miss binds to the wrong
    arithmetic and returns a number that looks entirely plausible, which is worse than an
    unsupported workbook because nothing looks wrong.
    """

    table: str
    role: SheetRole
    requires_all: tuple[str, ...]
    rejects: tuple[str, ...] = ()
    precedence: int = 50


# Order matters and is a correctness property, not a preference: the calculation sheet also
# carries a daily-salary column, so it is matched before anything looser.
ROLE_RULES: Final[tuple[RoleRule, ...]] = (
    RoleRule(
        table="calculation",
        role=SheetRole.CALCULATION,
        requires_all=("employee_ref", "total_accrual", "fte_accrual"),
        precedence=10,
    ),
    RoleRule(
        table="je_posting",
        role=SheetRole.JE_POSTING,
        requires_all=("header_id", "account", "line_amount", "reversal_date"),
        precedence=20,
    ),
    RoleRule(
        table="je_staging",
        role=SheetRole.JE_STAGING,
        requires_all=("header_id", "account", "line_amount"),
        rejects=("reversal_date",),
        precedence=30,
    ),
    RoleRule(
        table="aggregation",
        role=SheetRole.AGGREGATION,
        requires_all=("fte_accrual", "non_fte_accrual"),
        rejects=("total_accrual", "account"),
        precedence=40,
    ),
    RoleRule(
        table="employee_master",
        role=SheetRole.MASTER_DATA,
        requires_all=("employee_ref", "staffing_type"),
        rejects=("total_accrual",),
        precedence=50,
    ),
    RoleRule(
        table="entity_master",
        role=SheetRole.MASTER_DATA,
        requires_all=("company_code", "company_name"),
        precedence=60,
    ),
)


@dataclass(frozen=True, slots=True)
class BindResult:
    """A sheet map and an honest account of what could not be bound."""

    sheet_map: SheetMap
    bound: tuple[str, ...]
    unbound_tables: tuple[str, ...]
    unresolved_headers: tuple[str, ...]
    identification: "Identification | None" = None
    """What tier 2 made of the workbook's shape. ``None`` when no catalogue was supplied."""

    @property
    def is_usable(self) -> bool:
        """A map without the calculation table cannot support a re-performance."""
        return "calculation" in self.bound


def bind_sheet_map(
    profile: WorkbookProfile,
    data: WorkbookData,
    je_types: Sequence["JETypeContext"] = (),
) -> BindResult:
    """Resolve roles and column meanings from the profile alone.

    ``je_types`` in precedence order turns the type itself into something decided rather
    than assumed. Supplied empty, the map reports the type as unknown: naming a type nothing
    was checked against is the one thing worse than naming none.
    """
    columns_by_table = {id(table): _meanings(table) for table in profile.tables}

    claimed: dict[str, TableProfile] = {}
    roles: dict[str, SheetRole] = {}
    for rule in sorted(ROLE_RULES, key=lambda item: item.precedence):
        for table in profile.tables:
            if rule.table in claimed or table.sheet in roles:
                continue
            meanings = columns_by_table[id(table)]
            if not set(rule.requires_all) <= set(meanings):
                continue
            if set(rule.rejects) & set(meanings):
                continue
            claimed[rule.table] = table
            roles[table.sheet] = rule.role

    for sheet in profile.sheets:
        roles.setdefault(sheet.name, _fallback_role(sheet.name))

    tables = {
        name: _table_ref(table, columns_by_table[id(table)]) for name, table in claimed.items()
    }
    unbound = tuple(sorted({rule.table for rule in ROLE_RULES} - set(claimed)))
    unresolved = _unresolved(profile, claimed.values())
    recognised = identify(je_types, profile, data) if je_types else Identification()

    logger.info(
        "sheet map bound tables=%s unbound=%s unresolved_headers=%d je_type=%s",
        sorted(tables),
        unbound,
        len(unresolved),
        recognised.je_type,
    )
    return BindResult(
        sheet_map=SheetMap(
            roles=roles,
            tables=tables,
            je_type=recognised.je_type,
            je_type_confidence=recognised.confidence,
        ),
        bound=tuple(sorted(tables)),
        unbound_tables=unbound,
        unresolved_headers=unresolved,
        identification=recognised,
    )


def _meanings(table: TableProfile) -> dict[str, str]:
    """Meaning -> column letter, first match winning.

    First match rather than last: these workbooks repeat labels in a scratch block to the
    right of the real one, and the leftmost occurrence is the one the entry is built from.
    """
    found: dict[str, str] = {}
    for column in table.columns:
        meaning = resolve_meaning(column.header)
        if meaning and meaning not in found:
            found[meaning] = column.letter
    return found


def _table_ref(table: TableProfile, columns: dict[str, str]) -> object:
    from review.models.workbook import TableRef

    return TableRef(
        sheet=table.sheet,
        header_row=table.header_row,
        first_row=table.first_row,
        last_row=table.last_row,
        columns=columns,
    )


def _fallback_role(sheet_name: str) -> SheetRole:
    """Only for sheets no column rule claimed. A guess here costs nothing: roles assigned
    this way are never used to bind a table, only to describe the file."""
    normalised = normalize_header(sheet_name)
    if "methodology" in normalised:
        return SheetRole.METHODOLOGY
    if "check" in normalised or "pivot check" in normalised:
        return SheetRole.SELF_CHECK
    return SheetRole.IGNORE


def _unresolved(profile: WorkbookProfile, bound: Iterable[TableProfile]) -> tuple[str, ...]:
    """Headers on a bound table that no alias knows.

    Reported rather than ignored: an unresolved header is usually one alias away from a
    column a check needs, and naming it turns an unsupported workbook into a two-minute fix.
    """
    unknown: set[str] = set()
    for table in bound:
        for column in table.columns:
            if normalize_header(column.header) and resolve_meaning(column.header) is None:
                unknown.add(column.header.replace("\n", " ").strip())
    return tuple(sorted(unknown))
