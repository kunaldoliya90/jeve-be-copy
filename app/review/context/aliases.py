"""The column vocabulary: every header text the harness has been taught to recognise.

Shared by the two deterministic tiers of identification — the shape discriminators in
``identify`` and the column binding in ``binder`` — which is why it sits in its own module
rather than inside either of them.

Adding a workbook whose columns are spelled differently is an entry here, not a code change.
That is the whole design intent: the alias table is the part a reviewer can extend without
touching arithmetic.
"""

from collections.abc import Sequence
from typing import Final

from review.utils.sheet_utils import normalize_header

# Meaning -> the header texts that have been seen to carry it, normalised. Adding a workbook
# whose columns are spelled differently is an entry here, not a code change.
ALIASES: Final[dict[str, tuple[str, ...]]] = {
    # identity and grouping
    "employee_ref": ("matricule", "personnel number", "employee id", "employee reference"),
    "employee_name": ("salari", "full name", "employee name"),
    # "co code length 4" is deliberately absent: it is a zero-padded text copy of the same
    # key, and binding it made the calculation and aggregation sheets group differently so
    # every roll-up hop reported a difference that was not there.
    "company_code": ("co code", "company code", "sap company"),
    "cost_center": ("cost center", "cost centre", "cost center code"),
    "currency": ("salary currency", "currency", "currency code", "posting currency"),
    "staffing_type": ("staffing type", "staffing type group"),
    "fte_classification": ("fte non fte", "fte non-fte", "categorization"),
    "start_date": ("sap company start date", "hire date"),
    # the CP leg
    "cp_earned": ("acquis cp n",),
    "cp_taken": ("pris cp n",),
    "cp_balance": ("solde cp n",),
    "cp_provision": ("total provision cp", "provision cp n"),
    "employer_charges": ("montant charges patronales", "taux charges patronales"),
    # the RTT leg
    "rtt_earned": ("acquis rtt", "earned rtt days"),
    "rtt_taken": ("pris rtt",),
    "rtt_balance": ("solde rtt",),
    # Kept apart from rtt_balance and rtt_provision on purpose: these three are the inputs
    # and the output of one multiplication, and binding them to the neighbouring columns
    # would re-perform a different sum that happens to look similar.
    "rtt_balance_days": ("balance rtt days",),
    "rtt_accrual": ("rtt accrual amount",),
    "rtt_provision": ("provision totale rtt",),
    "daily_salary_rate": ("daily salary rtt rate", "daily salary", "daily rate"),
    "months_to_accrue": ("months to accrue",),
    # the amounts that reach the entry
    "fte_accrual": ("fte vacation accrual", "sum of fte vacation accrual"),
    "non_fte_accrual": ("non fte vacation accrual", "sum of non fte vacation accrual"),
    "total_accrual": ("total accrual",),
    # the posted entry
    "header_id": ("header id",),
    "account": ("account",),
    "line_amount": ("line amount", "sum of line amount"),
    "line_description": ("line description",),
    "posting_date": ("posting date",),
    "reversal_date": ("reversal date",),
    "fiscal_year": ("fiscal year",),
    "fiscal_period": ("fiscal period", "period"),
    # entity master
    "company_name": ("company name",),
    "liability_account": ("vacation accrual b s", "vacation accrual bs"),
    "fte_expense_account": ("vacation accrual fte p l",),
    "non_fte_expense_account": ("vacation accrual nonfte p l",),
}

_COLUMN_OF: Final[dict[str, str]] = {
    alias: meaning for meaning, aliases in ALIASES.items() for alias in aliases
}


def resolve_meaning(header: str) -> str | None:
    """The meaning an alias table entry gives a header, if any."""
    return _COLUMN_OF.get(normalize_header(header))


def known_meanings() -> Sequence[str]:
    return sorted(ALIASES)


__all__ = ["ALIASES", "known_meanings", "resolve_meaning"]
