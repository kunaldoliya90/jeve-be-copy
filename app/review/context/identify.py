"""Tier 2 of identification: which journal entry type this workbook is, decided by shape.

Tier 1 is the layout cache (step 7); tier 3 is AI-1 (step 8). This layer sits between them
and settles most workbooks on its own, with no model involved: a JE type declares the column
families a workbook must carry to be it, and the ones that rule it out.

Two rules in the design are enforced here rather than trusted:

**``rejects`` is the load-bearing half.** A near-miss that binds anyway re-performs the wrong
arithmetic and returns a number that looks entirely plausible, which is worse than an
unsupported workbook, because nothing looks wrong.

**A sheet's name is never a signal.** One sample workbook holds its whole calculation on a
tab called ``DENT``. The JE type says so in ``never: [tab_name_match]``, and no code path
below reads a sheet name for anything but reporting where something was found.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from review.context.aliases import ALIASES, resolve_meaning
from review.models.context import JETypeContext
from review.models.workbook import TableProfile, WorkbookProfile
from review.models.workbook_data import WorkbookData

logger = logging.getLogger(__name__)

UNKNOWN: Final = "unknown"
"""A workbook nothing recognises. It routes to a person, which is the honest outcome: a
confidently wrong classification does not."""

_VARIANT_MATCH_CONFIDENCE: Final = 0.6
"""A variant's required columns were all found. Enough to proceed, not enough to be sure:
the columns prove the shape, and the corroborating signals prove the purpose."""

_PER_CORROBORATION: Final = 0.1
_MAX_CONFIDENCE: Final = 0.95
"""Never 1.0. Nothing short of a person confirming it earns certainty."""


@dataclass(frozen=True, slots=True)
class Identification:
    """What the shape said, and everything that went into saying it."""

    je_type: str = UNKNOWN
    variant: str | None = None
    confidence: float = 0.0
    matched: tuple[str, ...] = ()
    """Required signals observed, the evidence for the answer."""
    corroborating: tuple[str, ...] = ()
    """Signals found outside the posting table. The posting tab's own labels are the
    preparer's claim; these are evidence."""
    unobservable: tuple[str, ...] = ()
    """Signals no rule here can evaluate. Named rather than quietly treated as absent."""
    rejected: tuple[str, ...] = field(default=())
    """``"<type>/<variant>: <signal>"`` per variant ruled out, for the audit trail."""

    @property
    def recognised(self) -> bool:
        return self.je_type != UNKNOWN


# --- signals a JE type may name -----------------------------------------------------------
#
# A signal is either a column meaning (the column exists somewhere in the workbook) or one
# of the predicates below, which ask something the column's presence alone does not.

_Predicate = Callable[["_Observed"], bool]


@dataclass(frozen=True, slots=True)
class _Observed:
    """What was measured, once, so every signal is evaluated against the same reading."""

    meanings: frozenset[str]
    outside_posting: frozenset[str]
    populated: frozenset[str]
    populated_outside: frozenset[str]

    def has(self, meaning: str) -> bool:
        return meaning in self.meanings

    def outside_only(self) -> "_Observed":
        """The same reading with the posting table removed.

        Predicates are evaluated against this view when a signal has to come from outside
        the posting table. Without it a predicate would answer from the whole workbook and
        quietly satisfy the one rule that exists to stop an identification resting on the
        preparer's own labels.
        """
        return _Observed(
            meanings=self.outside_posting,
            outside_posting=self.outside_posting,
            populated=self.populated_outside,
            populated_outside=self.populated_outside,
        )


PREDICATES: Final[dict[str, _Predicate]] = {
    # An entry has to post somewhere: an account column beside an amount column.
    "accounts_present": lambda observed: observed.has("account") and observed.has("line_amount"),
    # The split that decides which expense account a line reaches.
    "fte_classification_column": lambda observed: observed.has("fte_classification"),
    # A column of reversal dates with nothing in it is a template, not a reversing accrual.
    "reversal_date_populated": lambda observed: "reversal_date" in observed.populated,
}


def identify(
    je_types: Sequence[JETypeContext],
    profile: WorkbookProfile,
    data: WorkbookData,
) -> Identification:
    """The first JE type in precedence order whose shape this workbook matches.

    ``je_types`` arrives in the order the catalogue declares, so a specific shape is tested
    before a looser one that would also match it. Order here is a correctness property, not
    a preference.
    """
    observed = _observe(profile, data)
    rejected: list[str] = []
    unobservable: set[str] = set()

    for je_type in je_types:
        spec = je_type.discriminators
        if spec is None:
            continue
        for variant in spec.variants:
            unobservable |= _unknown_signals(variant.requires_all + variant.rejects)

            blocker = next(
                (signal for signal in variant.rejects if _signal(signal, observed)), None
            )
            if blocker is not None:
                rejected.append(f"{je_type.id}/{variant.id}: rejected by {blocker}")
                continue

            missing = [signal for signal in variant.requires_all if not _signal(signal, observed)]
            if missing:
                rejected.append(f"{je_type.id}/{variant.id}: missing {', '.join(missing)}")
                continue

            corroborating = tuple(
                signal for signal in spec.corroborating if _signal(signal, observed)
            )
            # Some corroborating signals are posting-table facts by nature — an account
            # column, a populated reversal date. They count, but only once at least one
            # signal has been found away from the posting tab: otherwise the whole
            # identification rests on labels the preparer wrote about their own work.
            from_outside = any(
                _signal(signal, observed, outside_posting_only=True)
                for signal in spec.corroborating
            )
            uplift = _PER_CORROBORATION * len(corroborating) if from_outside else 0.0
            confidence = min(_MAX_CONFIDENCE, _VARIANT_MATCH_CONFIDENCE + uplift)
            logger.info(
                "identified je_type=%s variant=%s confidence=%.2f corroborating=%s outside=%s",
                je_type.id,
                variant.id,
                confidence,
                list(corroborating),
                from_outside,
            )
            return Identification(
                je_type=je_type.id,
                variant=variant.id,
                confidence=confidence,
                matched=variant.requires_all,
                corroborating=corroborating,
                unobservable=tuple(sorted(unobservable)),
                rejected=tuple(rejected),
            )

    logger.info("no je type matched this workbook: %s", rejected or "no discriminators declared")
    return Identification(unobservable=tuple(sorted(unobservable)), rejected=tuple(rejected))


def _observe(profile: WorkbookProfile, data: WorkbookData) -> _Observed:
    """Read every signal's input once: which meanings exist, where, and which carry data."""
    meanings: set[str] = set()
    outside: set[str] = set()
    populated: set[str] = set()
    populated_outside: set[str] = set()

    for table in profile.tables:
        found = _meanings_on(table)
        filled = _populated_on(table, found, data)
        meanings |= set(found)
        populated |= filled
        if not _is_posting(found):
            outside |= set(found)
            populated_outside |= filled

    return _Observed(
        meanings=frozenset(meanings),
        outside_posting=frozenset(outside),
        populated=frozenset(populated),
        populated_outside=frozenset(populated_outside),
    )


def _meanings_on(table: TableProfile) -> dict[str, str]:
    """Meaning -> column letter for one table, leftmost occurrence winning."""
    found: dict[str, str] = {}
    for column in table.columns:
        meaning = resolve_meaning(column.header)
        if meaning and meaning not in found:
            found[meaning] = column.letter
    return found


def _is_posting(meanings: dict[str, str]) -> bool:
    """A posting table, recognised by its columns rather than by its tab name."""
    return "header_id" in meanings and "line_amount" in meanings


def _populated_on(table: TableProfile, meanings: dict[str, str], data: WorkbookData) -> set[str]:
    """Meanings whose column actually carries a value on at least one data row."""
    sheet = data.sheet(table.sheet)
    if sheet is None:
        return set()

    filled: set[str] = set()
    for meaning, letter in meanings.items():
        column = _column_index(letter)
        for row in range(table.first_row, min(table.last_row, table.first_row + 200) + 1):
            if not sheet.cell(row, column).is_blank:
                filled.add(meaning)
                break
    return filled


def _column_index(letter: str) -> int:
    index = 0
    for character in letter:
        index = index * 26 + (ord(character.upper()) - ord("A") + 1)
    return index


def _signal(name: str, observed: _Observed, *, outside_posting_only: bool = False) -> bool:
    """Evaluate one signal. An unobservable one is false, never true.

    Failing closed is the whole point: a signal nobody can evaluate must not satisfy a
    ``requires_all`` it appears in, and must not reject a type it appears against.
    """
    reading = observed.outside_only() if outside_posting_only else observed
    predicate = PREDICATES.get(name)
    if predicate is not None:
        return predicate(reading)
    if name not in _KNOWN_MEANINGS:
        return False
    return name in reading.meanings


def _unknown_signals(signals: Sequence[str]) -> set[str]:
    return {
        signal for signal in signals if signal not in PREDICATES and signal not in _KNOWN_MEANINGS
    }


_KNOWN_MEANINGS: Final = frozenset(ALIASES)


__all__ = ["PREDICATES", "UNKNOWN", "Identification", "identify"]
