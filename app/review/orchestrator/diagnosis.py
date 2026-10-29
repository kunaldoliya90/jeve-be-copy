"""Why a workbook could not be checked, written for the person who uploaded it.

A review that matches no entry type used to end as a blank page: no score, no checks, no
reason, and a progress bar at 100%. That is the one outcome a reviewer cannot act on — it
says neither *this file is wrong* nor *the harness does not know this shape yet*, and the
two need entirely different responses.

Everything here is already known by the time it is called: the binder said which tables it
could not claim and which headers it could not resolve, and the shape discriminators said
which candidate they ruled out and why. This module only turns that into sentences. It
decides nothing, calls no model, and never implies a check passed.
"""

import logging
from collections.abc import Sequence
from typing import Final

from review.context.aliases import ALIASES
from review.context.binder import ROLE_RULES, BindResult
from review.models.result import ReportPayload
from review.models.workbook import WorkbookProfile

logger = logging.getLogger(__name__)

MAX_LISTED_CANDIDATES: Final = 6
"""Rejected candidates named in full. Past this the list stops being read."""

MAX_LISTED_HEADERS: Final = 12
MAX_EXAMPLE_SPELLINGS: Final = 3
"""Header texts offered per unbound column meaning, as examples of what is recognised."""

_REQUIRED_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    rule.table: rule.requires_all for rule in ROLE_RULES
}


def diagnose(
    binding: BindResult,
    profile: WorkbookProfile,
    *,
    declared: str | None = None,
    catalogue: Sequence[str] = (),
) -> ReportPayload:
    """The report a review gets when nothing could be checked.

    ``declared`` is the entry type the uploader named, if any, and ``catalogue`` the types
    this build knows. Both change the advice: a file nobody named is usually a file whose
    type was never chosen, and that is a one-click fix rather than a workbook defect.
    """
    identification = binding.identification
    unknown_type = identification is None or not identification.recognised

    if not binding.is_usable:
        headline = (
            "The calculation table could not be found, so nothing could be re-performed. "
            "No check ran."
        )
    elif unknown_type:
        headline = "No entry type matched this workbook, so no check ran."
    else:  # pragma: no cover - the orchestrator calls this only on the two cases above
        headline = "This review ended before any check ran."

    return ReportPayload(
        headline=headline,
        summary=_summary(binding, profile, unknown_type=unknown_type),
        next_steps=(
            *_findings(binding),
            *_next_steps(
                binding, declared=declared, catalogue=catalogue, unknown_type=unknown_type
            ),
        ),
    )


def _findings(binding: BindResult) -> list[str]:
    """What stopped the review, ahead of what to do about it.

    These were their own list on the report — the one headed "what could not be validated" —
    and they are the whole reason this module exists: without them a workbook nobody could
    read reports no score, no checks and no reason. With that list gone they lead the next
    steps instead, which is where a reader looking for what to do about the file already
    goes. Stating the obstacle before the instruction keeps the pair readable: a reader told
    to add a header spelling first has to know which header went unrecognised.
    """
    identification = binding.identification
    findings: list[str] = []
    if identification is not None:
        findings.extend(
            f"Ruled out: {reason}." for reason in identification.rejected[:MAX_LISTED_CANDIDATES]
        )
        if identification.unobservable:
            findings.append(
                "No rule here can evaluate: "
                f"{', '.join(identification.unobservable)}. These were treated as absent."
            )
    findings.extend(_unbound_gaps(binding))
    findings.extend(_header_gaps(binding))
    return findings


def _summary(binding: BindResult, profile: WorkbookProfile, *, unknown_type: bool) -> str:
    """What was read, before what was not understood: the reader has to place the file."""
    named = f" ({', '.join(binding.bound)})" if binding.bound else ""
    sentences = [
        f"The file was read in full: {len(profile.sheets)} sheet(s) and "
        f"{len(profile.tables)} table(s). "
        f"{len(binding.bound)} of {len(_REQUIRED_COLUMNS)} expected tables were bound{named}."
    ]
    if unknown_type:
        sentences.append(
            "Nothing in the catalogue matched that shape, so no entry type's checks were applied."
        )
    sentences.append(
        "No check was run, which is not the same as a check that passed: nothing in this "
        "file has been validated either way."
    )
    return " ".join(sentences)


def _unbound_gaps(binding: BindResult) -> list[str]:
    """Each table the map wanted, and the column meanings that would have claimed it."""
    gaps: list[str] = []
    for table in binding.unbound_tables:
        needed = _REQUIRED_COLUMNS.get(table, ())
        if not needed:
            gaps.append(f"No table could be bound as '{table}'.")
            continue
        gaps.append(
            f"No table could be bound as '{table}': it needs one header for each of "
            f"{', '.join(_with_examples(meaning) for meaning in needed)}."
        )
    return gaps


def _with_examples(meaning: str) -> str:
    """A column meaning with header texts known to carry it, so the advice is concrete."""
    spellings = ALIASES.get(meaning, ())[:MAX_EXAMPLE_SPELLINGS]
    if not spellings:
        return meaning
    return f"{meaning} (e.g. {', '.join(repr(text) for text in spellings)})"


def _header_gaps(binding: BindResult) -> list[str]:
    """The headers that were there and meant nothing to the harness.

    These are the actionable half of an unmapped file: a column the alias table has never
    been taught is indistinguishable from a column that is not there.
    """
    if not binding.unresolved_headers:
        return []
    shown = binding.unresolved_headers[:MAX_LISTED_HEADERS]
    listed = ", ".join(repr(header) for header in shown)
    more = len(binding.unresolved_headers) - len(shown)
    tail = f", and {more} more" if more else ""
    return [
        f"{len(binding.unresolved_headers)} header(s) matched no known column meaning: "
        f"{listed}{tail}."
    ]


def _next_steps(
    binding: BindResult,
    *,
    declared: str | None,
    catalogue: Sequence[str],
    unknown_type: bool,
) -> list[str]:
    """What the person holding this file can actually do about it."""
    steps: list[str] = []
    if unknown_type and declared is None and catalogue:
        steps.append(
            "Upload again naming the entry type "
            f"({', '.join(catalogue)}). Its checks then run even when the shape is "
            "unfamiliar, and every result is reported as unconfirmed."
        )
    elif unknown_type and declared is not None and declared not in catalogue:
        steps.append(
            f"The entry type named on the upload, '{declared}', is not in this build's "
            f"catalogue ({', '.join(catalogue) or 'empty'}). Name one of those instead."
        )
    if "calculation" in binding.unbound_tables:
        steps.append(
            "Check the calculation tab is present and that its header row is a single row "
            "of labels above the data. Columns are bound by header text, never by position."
        )
    if binding.unresolved_headers:
        steps.append(
            "If those columns are the right ones under different names, their spellings "
            "have to be added to the column vocabulary before they can be bound. Send the "
            "header texts above to whoever maintains the entry type."
        )
    steps.append(
        "If this is an entry type the harness has never seen, it needs a type file with "
        "its discriminators before any of it can be checked."
    )
    return steps


__all__ = ["diagnose"]
