"""Tools that check the arithmetic of the posted entry itself.

All of them work in ``Decimal`` and treat differences within the agreed tolerance as zero:
spreadsheet rounding on a 27.9m entry produces a few thousandths of a cent, and failing an
otherwise clean entry on that would be noise a reviewer learns to ignore.
"""

from collections import defaultdict
from decimal import Decimal
from itertools import pairwise
from typing import Any, NamedTuple

from review.models.evidence import Figure
from review.tools.base import BaseTool, BoundTable, ToolContext, ToolError, worst_first


class _GroupDifference(NamedTuple):
    """A group whose amounts do not net to zero."""

    group: str
    difference: Decimal

    def as_sample(self) -> dict[str, Any]:
        return {"group": self.group, "difference": self.difference}


class _HopDifference(NamedTuple):
    """One group compared across a single roll-up hop."""

    group: str
    from_total: Decimal
    to_total: Decimal
    difference: Decimal
    one_sided: bool

    def as_sample(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "from": self.from_total,
            "to": self.to_total,
            "difference": self.difference,
            "one_sided": self.one_sided,
        }


class BalanceTool(BaseTool):
    """Do debits equal credits, overall and within each group?

    Grouping matters as much as the total. An entry can net to zero across the file while a
    single company code is out by the same amount in both directions, and only the grouped
    view shows it.
    """

    name = "balance"
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "Table name from the sheet map"},
        "group_by": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Column meanings to group on, e.g. company_code and currency",
        },
        "amount": {"type": "string", "description": "Column meaning holding the signed amount"},
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "je_posting",
        group_by: list[str] | None = None,
        amount: str = "line_amount",
        **_: Any,
    ) -> list[Figure]:
        bound = ctx.table(table)
        keys = list(group_by or [])
        args = {"table": table, "group_by": keys, "amount": amount}

        overall = Decimal(0)
        groups: dict[tuple[str, ...], Decimal] = defaultdict(Decimal)
        unreadable = 0

        for row in bound.rows:
            value = bound.number(row, amount)
            if value is None:
                unreadable += 1
                continue
            overall += value
            if keys:
                groups[bound.key(row, keys)] += value

        offending: list[_GroupDifference] = [
            _GroupDifference(group=" / ".join(key) or "(all)", difference=total)
            for key, total in sorted(groups.items())
            if not ctx.is_zero(total)
        ]
        impact = sum((abs(item.difference) for item in offending), Decimal(0))
        balanced = ctx.is_zero(overall) and not offending

        summary = (
            f"Debits and credits net to {overall} across {bound.row_count:,} lines"
            f"{f' in {len(groups)} groups' if keys else ''}"
            f"{f'; {len(offending)} group(s) do not net to zero' if offending else ''}"
            f"{f'; {unreadable} line(s) held no readable amount' if unreadable else ''}"
        )
        return [
            self.figure(
                check_id,
                ok=balanced,
                summary=summary,
                exception_count=len(offending) + unreadable,
                financial_impact=impact or None,
                samples=worst_first([item.as_sample() for item in offending]),
                numbers=[overall, impact, *(item.difference for item in offending)],
                args=args,
            )
        ]


class ControlTotalsTool(BaseTool):
    """Do the totals the preparer wrote down equal the sum of the lines?

    The stated total is the preparer's own control. Where it is stale or hardcoded, every
    other check can pass while the entry does not say what it posts.
    """

    name = "control_totals"
    signature = {  # noqa: RUF012
        "table": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "amount": {"type": "string"},
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "je_posting",
        facts: list[str] | None = None,
        amount: str = "line_amount",
        **_: Any,
    ) -> list[Figure]:
        bound = ctx.table(table)
        names = list(facts or ["hash_total", "je_total"])
        args = {"table": table, "facts": names, "amount": amount}

        computed = sum(
            (value for row in bound.rows if (value := bound.number(row, amount)) is not None),
            Decimal(0),
        )
        absolute = sum(
            (abs(value) for row in bound.rows if (value := bound.number(row, amount)) is not None),
            Decimal(0),
        )
        debits = sum(
            (
                value
                for row in bound.rows
                if (value := bound.number(row, amount)) is not None and value > 0
            ),
            Decimal(0),
        )

        figures: list[Figure] = []
        for name in names:
            stated = _as_decimal(ctx.facts.get(name))
            if stated is None:
                figures.append(
                    self.figure(
                        check_id,
                        ok=None,
                        summary=f"{name} is not stated on the entry, so nothing can be compared",
                        decisive=False,
                        args={**args, "fact": name},
                        error=f"{name}_not_stated",
                    )
                )
                continue

            # Three readings are in use for a stated total on these entries, and guessing
            # wrong raises a false exception on a perfectly balanced file: the net of every
            # line, the gross of their absolute values, and the debit side alone — which is
            # what a balanced accrual's hash total usually is.
            candidates = {
                "net": stated - computed,
                "gross": stated - absolute,
                "debit side": stated - debits,
            }
            reading, difference = min(candidates.items(), key=lambda item: abs(item[1]))
            agrees = ctx.is_zero(difference)
            figures.append(
                self.figure(
                    check_id,
                    ok=agrees,
                    summary=(
                        f"{name} states {stated}; the lines give {computed} net, "
                        f"{absolute} gross, {debits} on the debit side"
                        + (
                            f", agrees on the {reading}"
                            if agrees
                            else f", closest reading is the {reading}, out by {difference}"
                        )
                    ),
                    exception_count=0 if agrees else 1,
                    financial_impact=None if agrees else abs(difference),
                    numbers=[stated, computed, absolute, debits, difference],
                    args={**args, "fact": name},
                )
            )
        return figures


class RollupChainTool(BaseTool):
    """Does each hop from the calculation to the posting carry the same money?

    Validated hop by hop on purpose. A grand total that agrees proves nothing when two hops
    are wrong in opposite directions, and that is exactly the error aggregation produces.
    """

    name = "rollup_chain"
    signature = {  # noqa: RUF012
        "hops": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Table names in order, e.g. calculation, aggregation, je_posting",
        },
        "group_by": {"type": "array", "items": {"type": "string"}},
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        hops: list[str] | None = None,
        group_by: list[str] | None = None,
        **_: Any,
    ) -> list[Figure]:
        names = list(hops or [])
        if len(names) < 2:
            raise ToolError("a roll-up needs at least two tables to compare")
        keys = list(group_by or ["company_code"])
        args = {"hops": names, "group_by": keys}

        present = [(name, ctx.optional_table(name)) for name in names]
        bound = [(name, table) for name, table in present if table is not None]
        missing = [name for name, table in present if table is None]
        if len(bound) < 2:
            raise ToolError(f"only {len(bound)} of {len(names)} tables in the chain were found")

        figures: list[Figure] = []
        for (from_name, from_table), (to_name, to_table) in pairwise(bound):
            figures.append(
                self._compare(ctx, check_id, from_name, from_table, to_name, to_table, keys, args)
            )

        if missing:
            figures.append(
                self.figure(
                    check_id,
                    ok=None,
                    summary=f"Hops not validated, table(s) not found: {', '.join(missing)}",
                    decisive=False,
                    args=args,
                    error="hop_table_missing",
                )
            )
        return figures

    def _compare(
        self,
        ctx: ToolContext,
        check_id: str,
        from_name: str,
        from_table: BoundTable,
        to_name: str,
        to_table: BoundTable,
        keys: list[str],
        args: dict[str, Any],
    ) -> Figure:
        left = _totals_by_key(from_table, keys)
        right = _totals_by_key(to_table, keys)

        differences = [
            _HopDifference(
                group=" / ".join(key) or "(all)",
                from_total=left.get(key, Decimal(0)),
                to_total=right.get(key, Decimal(0)),
                difference=left.get(key, Decimal(0)) - right.get(key, Decimal(0)),
                one_sided=key not in left or key not in right,
            )
            for key in sorted(set(left) | set(right))
        ]
        offending = [item for item in differences if not ctx.is_zero(item.difference)]
        impact = sum((abs(item.difference) for item in offending), Decimal(0))

        return self.figure(
            check_id,
            ok=not offending,
            summary=(
                f"{from_name} -> {to_name}: {len(differences)} group(s) compared, "
                f"{len(offending)} differ"
                f"{f' by {impact} in total' if offending else ''}"
            ),
            exception_count=len(offending),
            financial_impact=impact or None,
            samples=worst_first([item.as_sample() for item in offending]),
            numbers=[
                sum(left.values(), Decimal(0)),
                sum(right.values(), Decimal(0)),
                impact,
                *(item.difference for item in offending),
            ],
            args={**args, "hop": f"{from_name}->{to_name}"},
        )


def _totals_by_key(table: BoundTable, keys: list[str]) -> dict[tuple[str, ...], Decimal]:
    """Sum whichever amount column this table actually carries.

    Tables along a roll-up name their money differently — a calculation sheet has a total
    accrual, a posting sheet has a line amount — so the meaning is resolved per table rather
    than demanded of all of them.
    """
    amounts = _amount_columns(table)
    if not amounts:
        raise ToolError(f"{table.name!r} has no column bound to an amount")

    usable = [key for key in keys if table.has(key)]
    net: dict[tuple[str, ...], Decimal] = defaultdict(Decimal)
    debit: dict[tuple[str, ...], Decimal] = defaultdict(Decimal)
    for row in table.rows:
        key = table.key(row, usable)
        # A row with no group key is a subtotal or a spacer, not a record. Counting it
        # against a named group on the other side reports a difference that is not there.
        if usable and not any(part for part in key):
            continue
        for meaning in amounts:
            value = table.number(row, meaning)
            if value is None:
                continue
            net[key] += value
            if value > 0:
                debit[key] += value

    # A posted entry carries both sides of itself, so it nets to zero while the working it
    # came from is one-sided. Comparing the two as they stand reports the whole entry as a
    # difference. Where a table balances against itself, its debit side is the comparable
    # figure; where it does not, the net is.
    balanced = sum(net.values(), Decimal(0))
    one_sided = sum(debit.values(), Decimal(0))
    if abs(balanced) < one_sided / 1000 and one_sided > 0:
        return dict(debit)
    return dict(net)


def _amount_columns(table: BoundTable) -> list[str]:
    """Which column, or columns, carry this table's money.

    A posting sheet has one. An aggregation sheet splits the same money across an FTE and a
    non-FTE column, and summing only one of them understates every hop it takes part in —
    which reads as a reconciliation failure rather than as the mistake it is.
    """
    for single in ("line_amount", "total_accrual", "amount"):
        if table.has(single):
            return [single]
    split = [meaning for meaning in ("fte_accrual", "non_fte_accrual") if table.has(meaning)]
    return split


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except (ArithmeticError, ValueError):
            return None
    return None
