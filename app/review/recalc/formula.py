"""The approved formula: parsed once at startup, evaluated per row in Decimal.

The business states a journal entry's formula as an expression over named inputs -
``max(rtt_balance_days, 0) * daily_salary_rate`` - and this module turns that text into
something that can be evaluated two million times without surprises.

Three decisions hold it together.

**It is parsed, never evaluated as Python.** No ``eval``, no ``exec``, no name lookup into
anything but the row's own inputs. The grammar is four arithmetic operators, six
comparisons, six functions, numeric literals and parentheses, and a formula that uses
anything else fails to load - at startup, naming the JE type file, rather than halfway
through somebody's review.

**A missing input propagates.** Every operand is ``Decimal | None`` and anything touching a
``None`` is ``None``. A row an input does not resolve on therefore produces no amount at all,
which is what lets the engine count it as incomplete instead of recomputing it as though the
input were zero. Division by a zero or absent denominator is the same thing: no answer.
``coalesce`` is the one deliberate exception, and only because a blank cell is sometimes the
business's own answer rather than a gap: see its note below.

**It compiles to closures.** The parse tree is turned into nested callables once, so
evaluating a row costs the arithmetic and a few function calls rather than a walk over an
AST with a type test at every node. On a 37,613-row working that is the difference between
a work paper and a timeout.
"""

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, DivisionByZero, InvalidOperation
from functools import lru_cache
from typing import Final

Row = Mapping[str, Decimal | None]
"""One record's inputs, by the id the formula refers to them by."""

Term = Callable[[Row], Decimal | None]
"""A compiled expression. Returns ``None`` wherever an input it needs is absent."""


class FormulaError(ValueError):
    """A formula that cannot be parsed, or that names something it was not given.

    Raised at load time only. The business file is the input, so this is a startup failure
    naming the field - never something a review discovers about itself halfway through.
    """


_TOKENS: Final = re.compile(
    r"""
    \s*(?:
        (?P<number>\d+(?:\.\d+)?)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>==|!=|<=|>=|[+\-*/(),<>])
    )
    """,
    re.VERBOSE,
)
"""Two-character comparisons are listed before the single-character class on purpose: the
alternation is ordered, so ``<=`` would otherwise be read as ``<`` followed by ``=``."""

_TRUE: Final = Decimal(1)
_FALSE: Final = Decimal(0)

_COMPARISONS: Final[dict[str, Callable[[Decimal, Decimal], bool]]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
"""The comparisons a condition may be written with, answering 1 or 0.

All six rather than only the one a JE type happens to need today: a business that can write
``!=`` but not ``>`` would send the next rule back through a code change, and partial
comparison support is the kind of gap nobody discovers until they hit it. They bind looser
than ``+``, and exactly one is allowed per expression - ``a < b < c`` is a parse error
rather than a guess at which of the two readings the business meant.
"""

_FUNCTIONS: Final[dict[str, tuple[int, int]]] = {
    "max": (2, 8),
    "min": (2, 8),
    "abs": (1, 1),
    "round": (2, 2),
    "coalesce": (2, 2),
    "if": (3, 3),
}
"""The only functions a formula may call, with how many arguments each takes.

``max`` is the floor the business applies to a leave balance, ``min`` its cap, ``abs`` the
sign convention, ``round`` the currency's own scale. ``coalesce(value, default)`` is the one
deliberate hole in "a missing input propagates": verified on both sample workbooks that one
leg of a two-leg provision, typed independently by the payroll extract, is blank in exactly
the periods that leg does not apply, and the business's own stated total simply omits it
rather than reading as incomplete - so folding that blank to zero here is reproducing what
the business already does, not assuming it. It is written explicitly in the business's own
expression for that reason: the alternative, this behaviour hidden inside the engine for
every formula, is the thing ``None`` propagating everywhere else exists to prevent.

``if(condition, then, otherwise)`` is here because a real convention turned out not to be
expressible without it: the RTT leg of the French vacation accrual is nil for an employee
who has taken none of it, and nothing built from the four operators reproduces that. The
nearest arithmetic stand-in, scaling the leg by ``min(days_taken, 1)``, is right only while
nobody takes a half day - it was wrong on three records across the two real workbooks. The
untaken branch is not evaluated, so an input only one branch reads cannot make the other
branch's rows incomplete. Anything beyond these six belongs in the JE type's rules, not in
an expression nobody can test.
"""


class CompiledFormula:
    """A parsed expression, with the input ids it actually reads."""

    __slots__ = ("_term", "names", "text")

    def __init__(self, text: str, term: Term, names: frozenset[str]) -> None:
        self.text = text
        self.names = names
        self._term = term

    def __call__(self, row: Row) -> Decimal | None:
        return self._term(row)

    def evaluate(self, row: Row) -> Decimal | None:
        """The formula on one record, or ``None`` where an input it needs is absent."""
        return self._term(row)


@lru_cache(maxsize=256)
def compile_formula(text: str, allowed: frozenset[str] = frozenset()) -> CompiledFormula:
    """Parse one expression, checking every name against what the row will carry.

    Cached on the text and the names allowed in it. A compiled formula is immutable and
    reads nothing but the row it is handed, so the JE type file's formulas are parsed once
    per process however many reviews run - the business file validates every one of them at
    startup, and every review after that is handed the same object.

    ``allowed`` empty means any name is accepted, which is only used by tests; a JE type
    always passes its declared inputs, so a typo in the business file is a startup error
    rather than a term that silently evaluates to nothing on every row.
    """
    parser = _Parser(text, allowed)
    term = parser.parse()
    return CompiledFormula(text=text.strip(), term=term, names=frozenset(parser.names))


def substitute(text: str, labels: Mapping[str, str]) -> str:
    """The same formula with each input id replaced by that column's header text.

    A substitution and never a translation, for the reason the category split work paper
    gives: prose invented for an arbitrary formula is how a wrong explanation ends up under
    a right number. A name with no label is left exactly as the business wrote it.
    """

    def swap(match: re.Match[str]) -> str:
        name = match.group(0)
        if text[match.end() :].lstrip()[:1] == "(":
            # A function, not an input. Nothing may rename max() into a column header, even
            # if a business file happens to declare an input called "max".
            return name
        label = labels.get(name)
        return label if label else name

    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", swap, text).strip()


def expand(text: str, stages: Sequence[tuple[str, str]], labels: Mapping[str, str]) -> str:
    """The formula with every derived stage written out, down to the columns themselves.

    ``substitute`` alone leaves a staged formula half-named: a stage has no column, so it
    keeps the id or the label the business gave it, and a reader is told the entry is
    "CP provision plus the rebuilt RTT accrual" without being told which columns the rebuilt
    figure comes from. This writes the whole thing out in the preparer's own headers, so the
    columns the arithmetic actually reads are on the page and change with the workbook.

    ``stages`` are ``(id, expression)`` in declaration order, which is what makes one pass
    enough: a stage may only read inputs and stages declared before it, so by the time one
    is reached every stage it names has already been written out.

    Each stage is wrapped in parentheses as it goes in. Inlining ``a + b`` into ``x + s / 12``
    unparenthesised would silently produce ``x + a + b / 12``, which is a different sum - the
    displayed formula would no longer be the one that was evaluated, which is worse than a
    formula nobody can read.
    """
    resolved: dict[str, str] = {}
    for stage_id, expression in stages:
        resolved[stage_id] = f"({substitute(expression, {**labels, **resolved})})"
    return substitute(text, {**labels, **resolved})


# --------------------------------------------------------------------------- parsing


class _Parser:
    """Precedence climbing over the token list. Produces closures, not nodes."""

    def __init__(self, text: str, allowed: frozenset[str]) -> None:
        self._text = text
        self._allowed = allowed
        self._tokens = _tokenise(text)
        self._at = 0
        self.names: set[str] = set()

    # --- the grammar ------------------------------------------------------------------

    def parse(self) -> Term:
        term = self._expression()
        if self._at < len(self._tokens):
            raise FormulaError(f"unexpected {self._peek()!r} in formula {self._text!r}")
        return term

    def _expression(self) -> Term:
        """The loosest level: one optional comparison over two arithmetic expressions.

        Deliberately not a loop. A chained ``a < b < c`` has two defensible readings and no
        business has asked for either, so it is left as an unconsumed token and reported as
        a parse error at startup.
        """
        left = self._sum()
        symbol = self._peek()
        if symbol in _COMPARISONS:
            self._take()
            return _compare(symbol, left, self._sum())
        return left

    def _sum(self) -> Term:
        left = self._product()
        while self._peek() in {"+", "-"}:
            sign = self._take()
            right = self._product()
            left = _add(left, right) if sign == "+" else _subtract(left, right)
        return left

    def _product(self) -> Term:
        left = self._unary()
        while self._peek() in {"*", "/"}:
            sign = self._take()
            right = self._unary()
            left = _multiply(left, right) if sign == "*" else _divide(left, right)
        return left

    def _unary(self) -> Term:
        if self._peek() == "-":
            self._take()
            return _negate(self._unary())
        if self._peek() == "+":
            self._take()
        return self._primary()

    def _primary(self) -> Term:
        symbol = self._take()
        if symbol == "(":
            inner = self._expression()
            self._expect(")")
            return inner
        if _is_number(symbol):
            return _constant(Decimal(symbol))
        if not _is_name(symbol):
            raise FormulaError(f"unexpected {symbol!r} in formula {self._text!r}")
        if self._peek() == "(":
            return self._call(symbol)
        if self._allowed and symbol not in self._allowed:
            raise FormulaError(
                f"formula {self._text!r} reads {symbol!r}, which is not a declared input"
            )
        self.names.add(symbol)
        return _lookup(symbol)

    def _call(self, name: str) -> Term:
        arity = _FUNCTIONS.get(name)
        if arity is None:
            raise FormulaError(
                f"formula {self._text!r} calls {name!r}; only {', '.join(sorted(_FUNCTIONS))} "
                "may be called"
            )
        self._expect("(")
        args = [self._expression()]
        while self._peek() == ",":
            self._take()
            args.append(self._expression())
        self._expect(")")
        low, high = arity
        if not low <= len(args) <= high:
            raise FormulaError(
                f"{name}() in formula {self._text!r} takes "
                f"{low if low == high else f'{low} to {high}'} arguments, not {len(args)}"
            )
        return _CALLS[name](args)

    # --- the token cursor -------------------------------------------------------------

    def _peek(self) -> str:
        return self._tokens[self._at] if self._at < len(self._tokens) else ""

    def _take(self) -> str:
        if self._at >= len(self._tokens):
            raise FormulaError(f"formula {self._text!r} ends in the middle of an expression")
        symbol = self._tokens[self._at]
        self._at += 1
        return symbol

    def _expect(self, symbol: str) -> None:
        if self._take() != symbol:
            raise FormulaError(f"expected {symbol!r} in formula {self._text!r}")


def _tokenise(text: str) -> list[str]:
    if not text.strip():
        raise FormulaError("a formula cannot be empty")
    tokens: list[str] = []
    at = 0
    while at < len(text):
        if text[at].isspace():
            at += 1
            continue
        match = _TOKENS.match(text, at)
        if match is None:
            raise FormulaError(f"cannot read {text[at]!r} in formula {text!r}")
        tokens.append(match.group("number") or match.group("name") or match.group("op"))
        at = match.end()
    return tokens


def _is_number(symbol: str) -> bool:
    return symbol[:1].isdigit()


def _is_name(symbol: str) -> bool:
    return symbol[:1].isalpha() or symbol[:1] == "_"


# --------------------------------------------------------------------------- closures


def _constant(value: Decimal) -> Term:
    def term(_: Row) -> Decimal:
        return value

    return term


def _lookup(name: str) -> Term:
    def term(row: Row) -> Decimal | None:
        return row.get(name)

    return term


def _add(left: Term, right: Term) -> Term:
    def term(row: Row) -> Decimal | None:
        first, second = left(row), right(row)
        return None if first is None or second is None else first + second

    return term


def _subtract(left: Term, right: Term) -> Term:
    def term(row: Row) -> Decimal | None:
        first, second = left(row), right(row)
        return None if first is None or second is None else first - second

    return term


def _multiply(left: Term, right: Term) -> Term:
    def term(row: Row) -> Decimal | None:
        first, second = left(row), right(row)
        return None if first is None or second is None else first * second

    return term


def _divide(left: Term, right: Term) -> Term:
    def term(row: Row) -> Decimal | None:
        first, second = left(row), right(row)
        if first is None or second is None or not second:
            # A denominator of zero is a figure this row does not have, not an error to
            # raise: the row is then counted as incomplete, like any other missing input.
            return None
        try:
            return first / second
        except (DivisionByZero, InvalidOperation):
            return None

    return term


def _compare(symbol: str, left: Term, right: Term) -> Term:
    """One comparison, answering 1 or 0 so a condition composes like any other term.

    ``None`` on either side is ``None``, like every other operator here: a row an input does
    not resolve on has no answer to the question, which is not the same as the answer being
    no. Reading an absent value as a failed test is how a missing column turns into a
    confident finding about every record.
    """
    test = _COMPARISONS[symbol]

    def term(row: Row) -> Decimal | None:
        first, second = left(row), right(row)
        if first is None or second is None:
            return None
        return _TRUE if test(first, second) else _FALSE

    return term


def _conditional(args: list[Term]) -> Term:
    """``if(condition, then, otherwise)``, evaluating only the branch it takes.

    Laziness is the point rather than an optimisation: the branch not taken may read an
    input this row does not carry, and evaluating it would make the row incomplete over a
    figure the business never asked for on it.
    """
    condition, when_true, when_false = args

    def term(row: Row) -> Decimal | None:
        answer = condition(row)
        if answer is None:
            return None
        return when_true(row) if answer else when_false(row)

    return term


def _negate(inner: Term) -> Term:
    def term(row: Row) -> Decimal | None:
        value = inner(row)
        return None if value is None else -value

    return term


def _maximum(args: list[Term]) -> Term:
    def term(row: Row) -> Decimal | None:
        values = [arg(row) for arg in args]
        return (
            None
            if any(value is None for value in values)
            else max(v for v in values if v is not None)
        )

    return term


def _minimum(args: list[Term]) -> Term:
    def term(row: Row) -> Decimal | None:
        values = [arg(row) for arg in args]
        return (
            None
            if any(value is None for value in values)
            else min(v for v in values if v is not None)
        )

    return term


def _absolute(args: list[Term]) -> Term:
    inner = args[0]

    def term(row: Row) -> Decimal | None:
        value = inner(row)
        return None if value is None else abs(value)

    return term


def _rounded(args: list[Term]) -> Term:
    value_of, places_of = args[0], args[1]

    def term(row: Row) -> Decimal | None:
        value, places = value_of(row), places_of(row)
        if value is None or places is None:
            return None
        try:
            return round(value, int(places))
        except (InvalidOperation, ValueError):
            return None

    return term


def _coalesce(args: list[Term]) -> Term:
    value_of, default_of = args[0], args[1]

    def term(row: Row) -> Decimal | None:
        value = value_of(row)
        return value if value is not None else default_of(row)

    return term


_CALLS: Final[dict[str, Callable[[list[Term]], Term]]] = {
    "max": _maximum,
    "min": _minimum,
    "coalesce": _coalesce,
    "abs": _absolute,
    "round": _rounded,
    "if": _conditional,
}


__all__ = [
    "CompiledFormula",
    "FormulaError",
    "Row",
    "Term",
    "compile_formula",
    "expand",
    "substitute",
]
