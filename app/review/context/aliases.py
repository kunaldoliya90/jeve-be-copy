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
    # "provision cp n" is deliberately absent from cp_provision: both files also carry a
    # "Total Provision CP" column one to the right of it, and first-match-wins would bind the
    # current period's provision alone (L) rather than the combined total (M) every other
    # figure here - the recalculation's worked example and the composition checks both - is
    # written over. It has its own meaning below instead, so the build-up (N-1 + N = total)
    # is itself checked rather than trusted.
    "cp_provision": ("total provision cp",),
    "cp_provision_prior": ("provision cp n 1",),
    "cp_provision_current": ("provision cp n",),
    # The prior period's own earned/taken/balance trio, beside the current period's above.
    # Declared so the balance the file states for it is re-performed rather than trusted.
    "cp_earned_prior": ("acquis cp n 1",),
    "cp_taken_prior": ("pris cp n 1",),
    "cp_balance_prior": ("solde cp n 1",),
    # A rate and an amount, not one meaning. "employer_charges" carried both phrases, so
    # first-match-wins bound the *rate* column and the amount was left unbound and
    # unchecked - the fourth instance of this defect, after cp_provision, rtt_balance and
    # rtt_earned. The entry excludes employer charges, so neither column reaches the posted
    # figure; both are re-performed anyway, because an edit to a column nothing reads is
    # still an edit to the working.
    "employer_charge_rate": ("taux charges patronales",),
    "employer_charge_amount": ("montant charges patronales",),
    "cp_provision_with_charges": ("provision totale",),
    # the RTT leg
    # "earned rtt days" is deliberately absent from rtt_earned: both French files carry
    # "Acquis RTT" (the yearly entitlement) *and* "Earned RTT Days" (that entitlement
    # prorated over the months worked), and first-match-wins made one meaning stand for two
    # different quantities. The recalculation rebuilds the prorated figure from the yearly
    # one, so it needs to read the first and compare against the second; with both behind
    # one meaning it would have compared a column against itself. Same defect, same fix, as
    # "provision cp n" against "total provision cp" above.
    "rtt_earned": ("acquis rtt",),
    "rtt_earned_days": ("earned rtt days",),
    "rtt_taken": ("pris rtt",),
    "rtt_balance": ("solde rtt",),
    # Kept apart from rtt_balance and rtt_provision on purpose: these three are the inputs
    # and the output of one multiplication, and binding them to the neighbouring columns
    # would re-perform a different sum that happens to look similar.
    "rtt_balance_days": ("balance rtt days",),
    "rtt_accrual": ("rtt accrual amount",),
    # The RTT leg's own provision trio: the base, its employer charges, and the total of the
    # two. ``rtt_provision`` is the total, and it is the one the discriminators test for, so
    # the base and the charge amount are named separately rather than folded into it.
    "rtt_provision_base": ("provision rtt",),
    "rtt_employer_charge_amount": ("montant charges patronales rtt",),
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
