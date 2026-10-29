"""The entry-level recalculation, as a tool: one work paper, two or three figures.

The arithmetic is in ``review/recalc/engine.py``. This is the thin part that turns what the
engine measured into the only currency a check's status is allowed to be set in - figures -
so the recalculation reaches a score through exactly the same routing rules as every other
check, with no special case anywhere in step 16, step 17 or the guardrails.

It is also where the work paper and the figures are produced *together*, from a single pass
over the rows. The orchestrator wants both: the figures for the check, the paper for the
reviewer. Running the engine twice to get them separately would walk 37,613 rows twice for
nothing, so ``run_recalculation`` returns the pair and the tool keeps only the figures.
"""

import logging
from decimal import Decimal
from typing import Any, Final, NamedTuple

from review.models.context import RecalculationSpec
from review.models.evidence import Figure, RowCoverage
from review.models.recalculation import DiscrepancyKind, EntryRecalculation, RecalcCitation
from review.models.verdicts import Status
from review.recalc.engine import recalculate
from review.tools.base import BaseTool, ToolContext, ToolError

logger = logging.getLogger(__name__)

TOOL_NAME: Final = "recalc_entry_total"
"""Named here because the orchestrator runs this one step apart from the rest of the plan."""


class Recalculated(NamedTuple):
    """One engine run: the work paper for the reviewer, the figures for the check."""

    paper: EntryRecalculation | None
    figures: list[Figure]


def run_recalculation(
    ctx: ToolContext, spec: RecalculationSpec | None, check_id: str, **args: Any
) -> Recalculated:
    """Regenerate the entry once, for both audiences, never raising upward.

    The failure modes are the tool contract's: a table the map never bound, a sheet that is
    not in the file, arithmetic a hostile workbook provokes. Each becomes a figure that
    cannot support a pass, and the check then reads as unable to validate - which is the
    honest answer, and the one the routing rules already know what to do with.
    """
    tool = RecalcEntryTotalTool()
    if spec is None:
        return Recalculated(
            None,
            [
                tool.unable(
                    check_id,
                    "this entry type states no approved formula, so the entry cannot be "
                    "regenerated from its inputs",
                    args,
                )
            ],
        )
    # The plan states the table and ``plan_check`` has already verified it is bound, which
    # is the whole point of declaring it as an argument: a recipe naming a table the tool
    # then ignores is the defect the plan check exists to catch.
    table = args.get("table")
    if isinstance(table, str) and table and table != spec.table:
        spec = spec.model_copy(update={"table": table})

    try:
        paper = recalculate(ctx, spec)
    except ToolError as exc:
        logger.info("recalculation unable on %s: %s", check_id, exc)
        return Recalculated(None, [tool.unable(check_id, str(exc), args)])
    except (ArithmeticError, TypeError, ValueError, KeyError, IndexError) as exc:
        logger.warning("recalculation failed on %s: %r", check_id, exc)
        return Recalculated(None, [tool.unable(check_id, f"{type(exc).__name__}: {exc}", args)])

    return Recalculated(paper, figures_for(tool, paper, check_id, args))


def figures_for(
    tool: BaseTool, paper: EntryRecalculation, check_id: str, args: dict[str, Any]
) -> list[Figure]:
    """The work paper as evidence: the entry, then the rows, then any corroboration.

    The first figure is decisive and it is the entry-level comparison, because that is the
    question - was the posted amount reproducible. The row-level figure is not decisive but
    it carries the coverage, so an entry that happens to agree while records are missing can
    never be read as proven: ``Figure.supports_pass`` enforces that, here as everywhere.
    """
    figures = [_entry_figure(tool, paper, check_id, args)]
    rows = _rows_figure(tool, paper, check_id, args)
    if rows is not None:
        figures.append(rows)
    figures.extend(_corroboration_figures(tool, paper, check_id, args))
    return figures


class RecalcEntryTotalTool(BaseTool):
    """Can the posted amount be regenerated from the inputs the business approved?

    The one check that establishes the entry's value rather than agreeing with it: every
    record is rebuilt from the approved formula in Decimal, summed, and set against what was
    actually booked - read from the posted entry's own lines, so the two sides are
    independent. A row the formula cannot be evaluated on is reported, never assumed to be
    zero, and a recalculation that covered only part of the population cannot support a pass.
    """

    name = TOOL_NAME
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "Table holding the records to rebuild"},
    }

    def run(self, ctx: ToolContext, check_id: str, **args: Any) -> list[Figure]:
        """Run the engine and keep the figures.

        ``je_type_args`` carries the approved formula, put there by the orchestrator: a tool
        argument is a column or a table name, never a methodology, and a formula arriving
        through the plan could be chosen by whatever wrote the plan.
        """
        spec = ctx.je_type_args.get("recalculation")
        if spec is not None and not isinstance(spec, RecalculationSpec):  # pragma: no cover
            raise ToolError("the recalculation basis handed to this tool is not a specification")
        return run_recalculation(ctx, spec, check_id, **args).figures


def _entry_figure(
    tool: BaseTool, paper: EntryRecalculation, check_id: str, args: dict[str, Any]
) -> Figure:
    """The comparison the check turns on: recomputed against booked, under the tolerance."""
    numbers: list[Decimal] = [paper.recomputed_total]
    if paper.booked.total is not None:
        numbers.append(paper.booked.total)
    if paper.difference is not None:
        numbers.extend([paper.difference, abs(paper.difference)])
    if paper.difference_pct is not None:
        # Quoted in the report as a percentage, so it has to be traceable as one.
        numbers.append(round(Decimal(paper.difference_pct), 4))
    numbers.extend([paper.basis.tolerance_amount, paper.row_impact])
    # Every count the explanation above states, because the explanation *is* this figure's
    # summary and step 19 lists any number in the report that traces to no figure. The
    # line count was the one that got missed: "summed from 426 of its own lines" reached
    # the written report and came back as an untraced number, which reads to a reviewer as
    # a figure not to be trusted.
    numbers.extend(
        Decimal(count)
        for count in (
            paper.rows_total,
            paper.rows_recomputed,
            paper.rows_agreeing,
            paper.rows_incomplete,
            paper.citation_count,
            paper.booked.lines,
        )
    )

    return tool.figure(
        check_id,
        # ``ok`` is the measurement, not the status: unable-to-compute stays None, so a
        # recalculation nobody could perform is never read as a failure.
        ok=None if paper.status is Status.UNABLE_TO_VALIDATE else paper.ties,
        summary=paper.explanation,
        decisive=True,
        exception_count=paper.citation_count,
        financial_impact=abs(paper.difference) if paper.difference else None,
        samples=[_entry_sample(paper)],
        numbers=numbers,
        args=args,
        error=None
        if paper.status is not Status.UNABLE_TO_VALIDATE
        else "recalculation_unavailable",
    )


def _entry_sample(paper: EntryRecalculation) -> dict[str, Any]:
    """The entry comparison as one exemplar, so the figure carries it for a reader."""
    return {
        "sheet": paper.basis.sheet,
        "formula": paper.basis.formula,
        "recomputed": paper.recomputed_total,
        "booked": paper.booked.total,
        "difference": paper.difference,
        "difference_pct": paper.difference_pct,
        "tolerance": paper.basis.tolerance_amount,
        "booked_from": paper.booked.description,
    }


def _rows_figure(
    tool: BaseTool, paper: EntryRecalculation, check_id: str, args: dict[str, Any]
) -> Figure | None:
    """The record-level half: how many rebuilt, how many agreed, and which rows did not.

    Carries the coverage, which is what stops a total that agrees over an incomplete
    population reading as proven. Not decisive: the entry-level figure above is.
    """
    if not paper.rows_total:
        return None

    differing = [
        citation for citation in paper.citations if citation.kind is DiscrepancyKind.STATED_DIFFERS
    ]
    # With no stated column there is nothing to tie a record *against*, so a record that
    # was rebuilt is as far as the evidence goes. Counting those as untied would report a
    # clean workbook as a table of failures.
    coverage = RowCoverage(
        rows_total=paper.rows_total,
        rows_bound=paper.rows_recomputed,
        rows_tied=paper.rows_agreeing if paper.basis.stated_column else paper.rows_recomputed,
    )
    return tool.figure(
        check_id,
        ok=coverage.complete,
        summary=(
            f"{paper.rows_recomputed:,} of {paper.rows_total:,} records rebuilt from the "
            f"approved formula, {paper.rows_agreeing:,} of them agreeing with the amount the "
            f"workbook states"
            + (f", {paper.citation_count:,} cited below" if paper.citation_count else "")
        ),
        decisive=False,
        exception_count=paper.citation_count,
        financial_impact=paper.row_impact or None,
        coverage=coverage,
        samples=[_citation_sample(citation) for citation in paper.citations],
        numbers=[paper.row_impact, *(c.difference for c in differing if c.difference is not None)],
        args=args,
    )


def _citation_sample(citation: RecalcCitation) -> dict[str, Any]:
    """One citation as a figure sample: cells and amounts, never an identifier."""
    return {
        "row": citation.row,
        "cell": citation.stated_cell,
        "kind": citation.kind.value,
        "recomputed": citation.recomputed,
        "stated": citation.stated,
        "difference": citation.difference,
        "why": citation.why,
    }


def _corroboration_figures(
    tool: BaseTool, paper: EntryRecalculation, check_id: str, args: dict[str, Any]
) -> list[Figure]:
    """Each term the workbook also computes for itself, re-performed and compared.

    The strongest evidence on the paper and never the decisive figure: it proves a leg of the
    formula was performed the way the business states, which is not the same claim as the
    entry reconciling.
    """
    return [
        tool.figure(
            check_id,
            ok=item.agrees,
            summary=(
                f"{item.term} re-performed from its own inputs agrees with "
                f"{item.label or item.against} on {item.rows_agreeing:,} of "
                f"{item.rows_compared:,} records"
                + (f", differing by {item.impact} in total" if item.impact else "")
            ),
            decisive=False,
            exception_count=item.rows_compared - item.rows_agreeing,
            financial_impact=item.impact or None,
            coverage=RowCoverage(
                rows_total=item.rows_compared,
                rows_bound=item.rows_compared,
                rows_tied=item.rows_agreeing,
            ),
            numbers=[item.impact],
            args={**args, "term": item.term},
        )
        for item in paper.corroborations
    ]


__all__ = ["TOOL_NAME", "RecalcEntryTotalTool", "Recalculated", "figures_for", "run_recalculation"]
