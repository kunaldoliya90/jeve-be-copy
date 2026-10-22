"""What a model is given, and the boundary that decides it.

Every input below is a *projection* of the case file, never the case file itself. Three
rules shape every one of them, and all three are enforced here rather than asked for in a
prompt:

**No workbook.** A model sees figures, counts and summaries that Python produced. It never
sees a sheet, and the only cell references it receives are locations, not contents.

**No employee data.** Samples are filtered to row numbers, cell references and amounts.
These workbooks redact names at source, but the boundary must hold for one that does not.

**No score weights.** A judging step that knew the weights could aim at a number. It is
given the question and the evidence, and nothing about what its answer is worth.
"""

from decimal import Decimal
from typing import Any, Final

from review.models.context import BusinessRules, CheckSpec, JETypeContext
from review.models.evidence import ExceptionCluster, Figure
from review.models.verdicts import Verdict

MAX_SAMPLES: Final = 6
"""Exemplars per figure. The full exception set stays in the evidence pack; a model needs
enough to describe a pattern, not enough to enumerate it."""

_SAMPLE_KEYS: Final = frozenset(
    {
        "row",
        "cell",
        "group",
        "difference",
        "expected",
        "actual",
        "days",
        "rate",
        "account",
        "lines",
        "period",
        "posting",
        "reversal",
        "one_sided",
        "from",
        "to",
        "issue",
        "stated",
    }
)
"""Keys a sample may carry across the boundary. An allowlist rather than a denylist: a new
tool adding an ``employee_name`` key must fail closed, not leak by default."""


def figure_payload(figure: Figure) -> dict[str, Any]:
    """One figure as a model may see it."""
    payload: dict[str, Any] = {
        "id": figure.figure_id,
        "tool": figure.tool,
        "ok": figure.ok,
        "decisive": figure.decisive,
        "summary": figure.summary,
        "exception_count": figure.exception_count,
        "numbers_you_may_quote": [str(number) for number in figure.numbers],
    }
    if figure.financial_impact is not None:
        payload["financial_impact"] = str(figure.financial_impact)
    if figure.coverage is not None:
        payload["rows"] = {
            "total": figure.coverage.rows_total,
            "compared": figure.coverage.rows_bound,
            "agreed": figure.coverage.rows_tied,
            "covers_every_row": figure.coverage.complete,
        }
    if figure.error:
        payload["could_not_compute"] = figure.error
    samples = [_safe_sample(sample) for sample in figure.samples[:MAX_SAMPLES]]
    if any(samples):
        payload["examples"] = [sample for sample in samples if sample]
    return payload


def _safe_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in sample.items()
        if key in _SAMPLE_KEYS
    }


def check_payload(
    check: CheckSpec,
    figures: tuple[Figure, ...],
    clusters: tuple[ExceptionCluster, ...] = (),
) -> dict[str, Any]:
    """One check: its question, who settles it, and the evidence it rests on.

    Where step 13b grouped the exceptions, the causes are sent as well. They are added, not
    substituted: the figures stay, because every number a verdict may quote has to come from
    them, and a cluster's prose is not a figure.
    """
    payload: dict[str, Any] = {
        "check_id": check.id,
        "question": check.question,
        "risk": check.risk,
        "decided_by": check.decided_by.value,
        "figures": [figure_payload(figure) for figure in figures],
    }
    if clusters:
        payload["exceptions_grouped_by_cause"] = [
            {
                "cause": item.cause,
                "affected": item.affected,
                "financial_impact": (
                    str(item.financial_impact) if item.financial_impact is not None else None
                ),
                "from_figures": list(item.figure_ids),
            }
            for item in clusters
        ]
    return payload


def business_context(je_type: JETypeContext, rules: BusinessRules) -> dict[str, Any]:
    """The standing context, placed first so the provider's prefix cache hits across reviews."""
    context: dict[str, Any] = {
        "journal_entry_type": je_type.name,
        "objective": je_type.objective,
        "tolerance": str(rules.tolerance),
        "known_patterns": list(je_type.known_patterns),
    }
    if je_type.methodology is not None:
        context["approved_methodology"] = {
            "salary_per_day_denominator": je_type.methodology.salary_per_day_denominator,
            "eligible_leave_types": list(je_type.methodology.eligible_leave_types),
            "balance_floored_at_zero": je_type.methodology.balance_floored_at_zero,
            "notes": list(je_type.methodology.notes),
        }
    return context


def judge_input(
    je_type: JETypeContext,
    rules: BusinessRules,
    checks: list[CheckSpec],
    pack: dict[str, tuple[Figure, ...]],
    facts: dict[str, Any],
    clusters: dict[str, tuple[ExceptionCluster, ...]] | None = None,
) -> dict[str, Any]:
    """Everything AI-3 is given. Note what is absent: the weights, and the workbook."""
    grouped = clusters or {}
    return {
        "context": business_context(je_type, rules),
        "facts_read_from_the_workbook": {name: str(value) for name, value in sorted(facts.items())},
        "checks": [
            check_payload(check, pack.get(check.id, ()), grouped.get(check.id, ()))
            for check in checks
        ],
    }


def challenge_input(
    je_type: JETypeContext,
    rules: BusinessRules,
    checks: list[CheckSpec],
    pack: dict[str, tuple[Figure, ...]],
    verdicts: dict[str, Verdict],
) -> dict[str, Any]:
    """Everything AI-4 is given: the same evidence, and the conclusions — but not the
    reasoning behind them.

    Withholding the rationale is the point of the step. A second reader shown the first
    one's argument tends to audit the argument; one shown only the evidence and the answer
    reads the evidence.
    """
    return {
        "context": business_context(je_type, rules),
        "checks": [
            {
                **check_payload(check, pack.get(check.id, ())),
                "conclusion_reached": (
                    {
                        "status": verdict.status.value,
                        "conclusion": verdict.conclusion,
                    }
                    if (verdict := verdicts.get(check.id)) is not None
                    else None
                ),
            }
            for check in checks
        ],
    }
