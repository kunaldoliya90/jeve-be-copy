"""Step 9. Whether AI-1's reading can be believed, and what follows when it cannot.

Four things are verified, and all four are verified against the workbook rather than against
the prompt:

1. **The type it names exists in the catalogue.** An id nobody serves is not a classification.
2. **Every cited cell exists and holds something.** A citation pointing at an empty cell is
   not evidence; it is a reference the reader would have had to check themselves.
3. **At least one citation sits outside the posting table.** The posting tab's own labels are
   the preparer's claim. The calculation, methodology and master tabs are evidence.
4. **It agrees with the shape rules.** Where it does, the recorded confidence rises. Where it
   does not, the review goes to a person.

What this never does is move a binding. Code's columns are what the tools read, agreed or
not, because a model that could move a binding could move the arithmetic.
"""

import logging
import re
from dataclasses import dataclass
from typing import Final

from review.agents.understand import Understanding
from review.context.identify import UNKNOWN, Identification
from review.models.sheet_map import SheetMap, SheetRole
from review.models.workbook import CellRef
from review.models.workbook_data import WorkbookData

logger = logging.getLogger(__name__)

_A1: Final = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")

CORROBORATED_CONFIDENCE: Final = 0.98
"""Both tiers agreed, and the agreement was cited from outside the posting table. Still not
1.0: only a person confirming it earns that."""

DISPUTED_CONFIDENCE: Final = 0.4
"""The two readings disagree. Below the shape's own 0.6, because a disputed answer is worth
less than an uncontested one — and a person is now looking at it either way."""


@dataclass(frozen=True, slots=True)
class MapVerdict:
    """What survived verification."""

    agrees: bool
    confidence: float
    citations: tuple[CellRef, ...] = ()
    """Only the citations that resolved to a populated cell."""
    notes: tuple[str, ...] = ()
    """Review-level observations, written for the person who will read the report."""

    @property
    def disputed(self) -> bool:
        return not self.agrees


def map_check(
    understanding: Understanding | None,
    recognised: Identification,
    sheet_map: SheetMap,
    data: WorkbookData,
) -> MapVerdict:
    """Verify AI-1 against the workbook and against the shape rules."""
    if understanding is None:
        # Two different facts, and saying the wrong one is worse than saying nothing: a
        # shape that settled the type with nobody to second it, or a shape that settled
        # nothing at all.
        note = (
            "The journal entry type was recognised from the workbook's shape alone; no "
            "second reading corroborated it."
            if recognised.recognised
            else "No second reading was available and the workbook's shape matched no entry "
            "type, so nothing here corroborates what this entry is."
        )
        return MapVerdict(agrees=True, confidence=recognised.confidence, notes=(note,))

    verified, unverified = _verify_citations(understanding.citations, data)
    outside = _outside_posting(verified, sheet_map)
    notes: list[str] = []

    if unverified:
        notes.append(
            f"{len(unverified)} citation(s) for the entry type point at cells that are "
            f"empty or absent: {', '.join(unverified[:4])}."
        )

    if understanding.je_type == recognised.je_type and recognised.recognised:
        if not outside:
            notes.append(
                "The second reading agreed on the entry type but cited only the posting "
                "tab, which carries the preparer's own labels rather than evidence."
            )
            return MapVerdict(
                agrees=True,
                confidence=recognised.confidence,
                citations=verified,
                notes=tuple(notes),
            )
        logger.info("AI-1 corroborated %s from outside the posting table", recognised.je_type)
        return MapVerdict(
            agrees=True,
            confidence=CORROBORATED_CONFIDENCE,
            citations=verified,
            notes=tuple(notes),
        )

    if understanding.je_type == UNKNOWN:
        notes.append(
            f"The shape rules read this as {recognised.je_type}, but the second reading "
            "could not place it. The checks below were run against "
            f"{recognised.je_type} and a reviewer should confirm that is right."
        )
    else:
        notes.append(
            f"The two readings disagree on what this entry is: the shape rules say "
            f"{recognised.je_type}, the second reading says {understanding.je_type}. The "
            f"checks below were run against {recognised.je_type}."
        )

    logger.warning(
        "map check disputed: shape=%s ai=%s confidence=%.2f",
        recognised.je_type,
        understanding.je_type,
        understanding.confidence,
    )
    return MapVerdict(
        agrees=False,
        confidence=DISPUTED_CONFIDENCE,
        citations=verified,
        notes=tuple(notes),
    )


def _verify_citations(
    citations: tuple[dict[str, str], ...], data: WorkbookData
) -> tuple[tuple[CellRef, ...], tuple[str, ...]]:
    """Split citations into the ones the workbook bears out and the ones it does not."""
    verified: list[CellRef] = []
    unverified: list[str] = []

    for citation in citations:
        sheet_name, reference = citation.get("sheet", ""), citation.get("cell", "")
        label = f"{sheet_name}!{reference}"
        position = _position(reference)
        sheet = data.sheet(sheet_name)
        if sheet is None or position is None:
            unverified.append(label)
            continue
        row, column = position
        if sheet.cell(row, column).is_blank:
            unverified.append(label)
            continue
        verified.append(CellRef(sheet=sheet_name, cell=reference.replace("$", "").upper()))

    return tuple(verified), tuple(unverified)


def _outside_posting(citations: tuple[CellRef, ...], sheet_map: SheetMap) -> tuple[CellRef, ...]:
    """Citations that are not on the posting or staging tabs."""
    claims = {
        sheet
        for sheet, role in sheet_map.roles.items()
        if role in {SheetRole.JE_POSTING, SheetRole.JE_STAGING}
    }
    return tuple(citation for citation in citations if citation.sheet not in claims)


def _position(reference: str) -> tuple[int, int] | None:
    """An A1 reference as (row, column), or ``None`` when it is not one."""
    match = _A1.match(reference.strip())
    if match is None:
        return None
    letters, digits = match.groups()
    column = 0
    for character in letters.upper():
        column = column * 26 + (ord(character) - ord("A") + 1)
    return int(digits), column


__all__ = ["CORROBORATED_CONFIDENCE", "DISPUTED_CONFIDENCE", "MapVerdict", "map_check"]
