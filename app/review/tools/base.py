"""The contract every tool keeps, and the helpers that make keeping it cheap.

A tool is a **pure function** over the parsed workbook and the case file: no I/O, no clock,
no randomness, no network. That is what lets a reviewer reopen a work paper in March and get
the same figure to the cent, and it is why the runner can fan tools out across threads
without thinking about it.

Two rules hold everywhere below.

**A tool never raises into the orchestrator.** A tool that fails returns a figure with
``ok: None`` and the error class on it. The check then reads as *unable to validate*, which
is an honest result; an exception escaping here would fail the whole review for one bad
column.

**A tool never decides a status.** It produces evidence. ``ok`` says whether what it measured
held, and the routing rules turn that into an outcome.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, Final, Protocol

from review.consts import FIGURE_SAMPLE_CAP
from review.models.context import BusinessRules, MethodologySpec
from review.models.evidence import Figure, MethodologyReading, PictureReading, RowCoverage
from review.models.review_context import ReviewContext
from review.models.sheet_map import SheetMap
from review.models.workbook import CellRef, Defect, TableRef
from review.models.workbook_data import CellValue, Sheet, WorkbookData
from review.utils.sheet_utils import column_index

logger = logging.getLogger(__name__)

FIGURE_ID: Final = "F-{index:02d}"


class ToolError(Exception):
    """Raised inside a tool and caught by ``run_tool``. Never seen by the orchestrator."""


class BoundTable:
    """A table resolved through the verified sheet map, ready to read.

    Tool arguments name *meanings* — ``{"table": "calculation", "column": "total_accrual"}``
    — never cell ranges. Resolving them here is what stops a tool reading a column the map
    never verified, and what makes the same tool work on two workbooks with different
    layouts.
    """

    __slots__ = ("_sheet", "name", "ref")

    def __init__(self, name: str, ref: TableRef, sheet: Sheet) -> None:
        self.name = name
        self.ref = ref
        self._sheet = sheet

    @property
    def rows(self) -> range:
        """The data rows, 1-based, header excluded."""
        return range(self.ref.first_row, self.ref.last_row + 1)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def has(self, meaning: str) -> bool:
        return meaning in self.ref.columns

    def value(self, row: int, meaning: str) -> CellValue:
        """One cell by meaning. Raises ``ToolError`` for a meaning the map did not bind."""
        letter = self.ref.columns.get(meaning)
        if letter is None:
            raise ToolError(f"{self.name!r} has no column bound to {meaning!r}")
        return self._sheet.cell(row, column_index(letter)).value

    def number(self, row: int, meaning: str) -> Decimal | None:
        """A numeric cell, or ``None`` when it is blank or not a number.

        ``None`` is never coerced to zero. A missing input is missing; patching it with a
        zero turns a data gap into a confident figure, and an Excel error value into one too.
        """
        value = self.value(row, meaning)
        return value if isinstance(value, Decimal) else None

    def text(self, row: int, meaning: str) -> str:
        value = self.value(row, meaning)
        return "" if value is None else str(value).strip()

    def key(self, row: int, meanings: Sequence[str]) -> tuple[str, ...]:
        """A grouping key, normalised so ``FR01`` and ``fr01 `` land together."""
        return tuple(self.text(row, meaning).casefold() for meaning in meanings)

    def cell_ref(self, row: int, meaning: str) -> CellRef:
        letter = self.ref.columns.get(meaning, "A")
        return CellRef(sheet=self.ref.sheet, cell=f"{letter}{row}")


class ToolContext:
    """Everything a tool may read. Deliberately narrow.

    A tool gets the workbook, the verified map, the facts code read from it, and the agreed
    rules. It does not get the model, the prompts or the other tools' figures.

    Two things here a model produced — ``methodology_read`` and ``picture_readings`` — and
    both are here for a reason that holds the rule rather than breaking it: nothing computes
    from either. They exist to be *compared*, the methodology against the basis the business
    approved and a screenshot's amounts against facts code read from the workbook. A
    difference is the finding. No arithmetic reads either one.
    """

    __slots__ = (
        "data",
        "defects",
        "facts",
        "figure_samples",
        "je_type_args",
        "methodology",
        "methodology_read",
        "picture_readings",
        "rules",
        "sheet_map",
    )

    def __init__(
        self,
        data: WorkbookData,
        sheet_map: SheetMap,
        rules: BusinessRules,
        facts: dict[str, Any] | None = None,
        je_type_args: dict[str, Any] | None = None,
        defects: tuple[Defect, ...] = (),
        methodology: MethodologySpec | None = None,
        methodology_read: MethodologyReading | None = None,
        picture_readings: dict[str, PictureReading] | None = None,
        figure_samples: int = FIGURE_SAMPLE_CAP,
    ) -> None:
        self.data = data
        self.sheet_map = sheet_map
        self.rules = rules
        self.facts = facts or {}
        self.je_type_args = je_type_args or {}
        self.defects = defects
        self.methodology = methodology
        self.methodology_read = methodology_read
        self.picture_readings = picture_readings or {}
        # ``config.FIGURE_SAMPLES`` when the orchestrator built this; the constant is the
        # harness default, for a context assembled without a deployment's settings.
        self.figure_samples = figure_samples

    @classmethod
    def from_review(
        cls, ctx: ReviewContext, data: WorkbookData, rules: BusinessRules
    ) -> "ToolContext":
        if ctx.sheet_map is None:
            raise ToolError("the case file has no verified sheet map")
        return cls(
            data=data,
            sheet_map=ctx.sheet_map,
            rules=rules,
            facts=ctx.facts,
            defects=ctx.profile.defects if ctx.profile else (),
        )

    def table(self, name: str) -> BoundTable:
        """Resolve a table by the name the JE type uses for it."""
        ref = self.sheet_map.tables.get(name)
        if ref is None:
            raise ToolError(f"the sheet map binds no table named {name!r}")
        sheet = self.data.sheet(ref.sheet)
        if sheet is None:
            raise ToolError(f"table {name!r} names sheet {ref.sheet!r}, which is not in the file")
        return BoundTable(name=name, ref=ref, sheet=sheet)

    def optional_table(self, name: str) -> BoundTable | None:
        try:
            return self.table(name)
        except ToolError:
            return None

    @property
    def tolerance(self) -> Decimal:
        return self.rules.tolerance

    def is_zero(self, amount: Decimal) -> bool:
        """Within the agreed tolerance. Spreadsheet rounding is not an exception."""
        return abs(amount) <= self.tolerance


class Tool(Protocol):
    """What the runner calls and what AI-2 is shown a signature of."""

    name: str
    signature: dict[str, Any]

    def run(self, ctx: ToolContext, check_id: str, **args: Any) -> list[Figure]: ...


class BaseTool(ABC):
    """Shared plumbing: argument naming and figure building.

    Sample capping is not done here: ``figure()`` has no context, and the cap is a
    setting. ``run_tool`` applies it to everything a tool returns.
    """

    name: str = ""
    signature: dict[str, Any] = {}  # noqa: RUF012 - a per-subclass schema, not shared state

    @abstractmethod
    def run(self, ctx: ToolContext, check_id: str, **args: Any) -> list[Figure]: ...

    def figure(
        self,
        check_id: str,
        *,
        ok: bool | None,
        summary: str,
        decisive: bool = True,
        exception_count: int = 0,
        financial_impact: Decimal | None = None,
        coverage: RowCoverage | None = None,
        samples: Iterable[dict[str, Any]] = (),
        numbers: Iterable[Decimal] = (),
        args: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Figure:
        """Build one figure. The id is a placeholder until the runner assigns plan order.

        ``numbers`` is the allowlist a verdict may quote. Anything a conclusion says that is
        not in here is an untraced number and sends the check to a person, so a tool must
        offer every figure it expects to be discussed — and nothing it has not measured.

        ``samples`` is kept whole here and capped by ``run_tool``, worst first, so a tool
        orders its exemplars without knowing the deployment's limit.
        """
        return Figure(
            figure_id="F-00",
            check_id=check_id,
            tool=self.name,
            args=args or {},
            ok=ok,
            decisive=decisive,
            exception_count=exception_count,
            financial_impact=financial_impact,
            coverage=coverage,
            samples=tuple(samples),
            summary=summary,
            numbers=tuple(dict.fromkeys(numbers)),
            error=error,
        )

    def unable(self, check_id: str, reason: str, args: dict[str, Any] | None = None) -> Figure:
        """A figure that could not be computed. Not a failure, and never a pass."""
        return self.figure(
            check_id, ok=None, summary=f"Could not be computed: {reason}", args=args, error=reason
        )


def run_tool(tool: BaseTool, ctx: ToolContext, check_id: str, **args: Any) -> list[Figure]:
    """Call a tool, converting any failure into an unable-to-validate figure.

    ``ToolError`` is the expected shape of a problem — a column the map did not bind, a sheet
    that is not there. The arithmetic errors below are the ones a hostile workbook produces:
    a division by a zero denominator, a value that is not the type the map claimed.

    This is also where a figure's exemplars are capped at ``ctx.figure_samples``: every
    figure a check produces passes through here.
    """
    try:
        figures = tool.run(ctx, check_id, **args)
        return [_cap_samples(figure, ctx.figure_samples) for figure in figures]
    except ToolError as exc:
        logger.info("tool %s unable on %s: %s", tool.name, check_id, exc)
        return [tool.unable(check_id, str(exc), args)]
    except (ArithmeticError, TypeError, ValueError, KeyError, IndexError) as exc:
        logger.warning("tool %s failed on %s: %r", tool.name, check_id, exc)
        return [tool.unable(check_id, f"{type(exc).__name__}: {exc}", args)]


def _cap_samples(figure: Figure, limit: int) -> Figure:
    """Keep at most ``limit`` exemplars, in the order the tool put them in.

    Only the examples shown to a reader are capped. ``exception_count`` and
    ``financial_impact`` are counted and priced over every row either way.
    """
    if len(figure.samples) <= limit:
        return figure
    return figure.model_copy(update={"samples": figure.samples[:limit]})


def assign_figure_ids(figures: Sequence[Figure]) -> tuple[Figure, ...]:
    """Number figures in plan order, so ``F-07`` means the same thing to every reader."""
    return tuple(
        figure.model_copy(update={"figure_id": FIGURE_ID.format(index=index)})
        for index, figure in enumerate(figures, start=1)
    )


def worst_first(samples: Iterable[dict[str, Any]], key: str = "difference") -> list[dict[str, Any]]:
    """Order exemplars by absolute size, so a capped sample shows the ones that matter."""

    def magnitude(sample: dict[str, Any]) -> Decimal:
        value = sample.get(key)
        return abs(value) if isinstance(value, Decimal) else Decimal(0)

    return sorted(samples, key=magnitude, reverse=True)
