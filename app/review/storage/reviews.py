"""Where a finished review lives, and the indexes beside it.

A review record is written **once and never updated**. That is the whole point of an audit
work product: the figure a reviewer cites in March must be the figure the system produced in
February, byte for byte. Nothing here offers an update method, so nothing can quietly rewrite
one.

``delete`` is not an exception to that and must not be read as one. Removing a record and
rewriting one are different acts: after a delete the review is gone and says so, where after
an update it would still be there saying something it never originally said. The first is
recoverable by re-running the file; the second is undetectable. So a record can be withdrawn
and never amended.

Writes go to a temporary file and are then renamed over the target. The rename is atomic on
every platform the service runs on, so a crash mid-write leaves the previous record intact
rather than a half-written one that parses and lies.
"""

import json
import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from review.models.recalculation import RecalcRow
from review.models.result import ReviewResult

IndexRow = dict[str, object]
"""One row of the listing index. Deliberately loose: it is a projection for display, and
pinning it to a model would make every stored index a migration."""

logger = logging.getLogger(__name__)

REVIEWS_DIR: Final = "reviews"
INDEX_FILE: Final = "index.json"
REUSE_FILE: Final = "reuse_index.json"
_TEMP_SUFFIX: Final = ".partial"
_ROWS_SUFFIX: Final = ".rows.json"
"""A review's full recalculation row set, beside its record rather than inside it: the main
record is read on every page view, and a reviewer browsing the grid is a different, much
heavier read that most page views never make."""


class ReviewStore:
    """Immutable JSON records on disk, with a small index for listing and reuse.

    Thread-safe because step 13 runs on a pool and several reviews run at once. The lock
    guards the indexes; the record files are write-once and need no guarding.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._records = root / REVIEWS_DIR
        self._lock = threading.Lock()

    def initialise(self) -> None:
        """Create the directories. Called once at startup, never on the request path."""
        self._records.mkdir(parents=True, exist_ok=True)
        for name in (INDEX_FILE, REUSE_FILE):
            path = self._root / name
            if not path.exists():
                _write_atomic(path, {})
        logger.info("review store ready at %s", self._root)

    # ---------------------------------------------------------------- records

    def write(self, result: ReviewResult, *, file_name: str, reuse_key: str | None) -> None:
        """Store a finished review. Refuses to overwrite one that already exists."""
        path = self._records / f"{result.review_id}.json"
        if path.exists():
            raise FileExistsError(f"review {result.review_id} is already stored and is immutable")

        _write_atomic(path, json.loads(result.model_dump_json()))
        with self._lock:
            index: dict[str, IndexRow] = self._read(INDEX_FILE)
            index[result.review_id] = {
                "review_id": result.review_id,
                "file_name": file_name,
                "je_type": result.je_type,
                "je_type_variant": result.entry.je_type_variant if result.entry else None,
                "period": result.entry.period if result.entry else None,
                "status": result.status,
                "score": result.score.total if result.score else None,
                "band": result.score.band if result.score else None,
                "provisional": result.score.provisional if result.score else False,
                "created_at": result.created_at.isoformat(),
                # The trend check's own baseline: the posted amount where it could be read,
                # the recomputed one otherwise. Carried on the index, rather than read from
                # the full record on every later review, so a baseline costs no more than a
                # listing does - the index is already read for every page of the review list.
                "booked_total": _decimal_str(
                    result.recalculation.booked.total if result.recalculation else None
                ),
                "recomputed_total": _decimal_str(
                    result.recalculation.recomputed_total if result.recalculation else None
                ),
            }
            _write_atomic(self._root / INDEX_FILE, index)

            # Only a review that actually completed is worth serving again. A failed or
            # cancelled one would hand back its own failure to the next identical upload.
            if reuse_key and result.status != "failed":
                reuse = self._reuse_index()
                reuse[reuse_key] = result.review_id
                _write_atomic(self._root / REUSE_FILE, reuse)

    def write_recalc_rows(self, review_id: str, rows: Sequence[RecalcRow]) -> None:
        """Store every record the recalculation walked. Refuses to overwrite, like ``write``.

        A sibling file rather than a field on the main record: the record is read on every
        page view of a review, and the full row set is a browse only some of those views
        ever make. Keeping them apart means that read stays the size it has always been.
        """
        path = self._records / f"{review_id}{_ROWS_SUFFIX}"
        if path.exists():
            raise FileExistsError(f"recalculation rows for {review_id} are already stored")
        _write_atomic(path, [json.loads(row.model_dump_json()) for row in rows])

    def read_recalc_rows(self, review_id: str) -> list[RecalcRow] | None:
        """Every record of a stored recalculation, or ``None`` when there is no such file.

        ``None`` here means "nothing was ever recalculated for this review" - the ordinary
        case for a JE type that states no formula - which is a different fact from an empty
        list, meaning the recalculation ran over zero records.
        """
        path = self._records / f"{review_id}{_ROWS_SUFFIX}"
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("recalculation rows for %s could not be read: %s", review_id, exc)
            return None
        if not isinstance(raw, list):
            logger.error("recalculation rows for %s are not a list", review_id)
            return None
        try:
            return [RecalcRow.model_validate(item) for item in raw]
        except ValueError as exc:
            logger.error("recalculation rows for %s could not be parsed: %s", review_id, exc)
            return None

    def delete(self, review_id: str) -> bool:
        """Remove one stored review. Returns False when there was nothing to remove.

        The indexes go first and the record file second, and that order is the only one
        that is safe. A listing row whose record has already been deleted points at a 404,
        which is the one state a reviewer cannot act on; an orphaned record file that no
        index mentions is invisible and harmless. If the unlink below fails, the review has
        still correctly disappeared from every path that reads it.

        The reuse index is purged by *value* rather than by a key computed here: the key
        depends on the submitted type and on three version strings, and recomputing it to
        delete it would mean the delete silently stopped working the day any of them
        changed. A stale entry would not corrupt a later review — ``find_by_reuse_key``
        reads the record and gets nothing — but it would keep a deleted id referenced.
        """
        with self._lock:
            index: dict[str, IndexRow] = self._read(INDEX_FILE)
            indexed = index.pop(review_id, None)
            if indexed is not None:
                _write_atomic(self._root / INDEX_FILE, index)

            reuse = self._reuse_index()
            stale = [key for key, value in reuse.items() if value == review_id]
            for key in stale:
                del reuse[key]
            if stale:
                _write_atomic(self._root / REUSE_FILE, reuse)

        path = self._records / f"{review_id}.json"
        existed = path.is_file()
        if existed:
            try:
                path.unlink()
            except OSError as exc:  # pragma: no cover - a permissions or locking fault
                logger.error("stored review %s could not be removed: %s", review_id, exc)

        rows_path = self._records / f"{review_id}{_ROWS_SUFFIX}"
        if rows_path.is_file():
            try:
                rows_path.unlink()
            except OSError as exc:  # pragma: no cover - a permissions or locking fault
                logger.error("recalculation rows for %s could not be removed: %s", review_id, exc)

        removed = existed or indexed is not None
        if removed:
            logger.info(
                "review %s deleted (record=%s index=%s reuse_keys=%d)",
                review_id,
                existed,
                indexed is not None,
                len(stale),
            )
        return removed

    def read(self, review_id: str) -> ReviewResult | None:
        path = self._records / f"{review_id}.json"
        if not path.is_file():
            return None
        try:
            return ReviewResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A record that will not parse is a bug worth seeing, not a 404 to shrug at.
            logger.error("stored review %s could not be read: %s", review_id, exc)
            return None

    def find_by_reuse_key(self, reuse_key: str) -> ReviewResult | None:
        with self._lock:
            found = self._reuse_index().get(reuse_key)
        return self.read(found) if found else None

    def _reuse_index(self) -> dict[str, str]:
        """Reuse key -> review id. A flat mapping, unlike the richer listing index."""
        path = self._root / REUSE_FILE
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("reuse index unreadable, continuing without it: %s", exc)
            return {}
        return (
            {str(key): str(value) for key, value in loaded.items()}
            if isinstance(loaded, dict)
            else {}
        )

    def trend_baseline(
        self,
        *,
        je_type: str,
        variant: str | None,
        before: datetime,
        months: int,
        exclude_period: str | None = None,
    ) -> list[Decimal]:
        """Prior periods' totals for this JE type, read off the index alone.

        One review per prior period, oldest and newest included, read from the listing
        index rather than the full stored record: the index already carries everything a
        baseline needs, and a review this reads is otherwise immutable, so there is nothing
        here a full read would make more current.

        Matched on variant only when a variant is given: a type with no variants of its own
        would otherwise compare against nothing, every time, forever. ``exclude_period``
        keeps a second review of the *same* fiscal period - a re-upload, or the same file
        identified two different ways - from reading as its own prior period: two reviews of
        FY26P07 are one data point for a trend, not two.

        Defined ahead of ``list()`` below on purpose: once that method's own name is bound
        in this class body, a bare ``list[...]`` written after it resolves to that method
        rather than to the builtin.
        """
        since = before - timedelta(days=30 * max(months, 1))
        with self._lock:
            index = self._read(INDEX_FILE)

        amounts: list[Decimal] = []
        for row in index.values():
            if row.get("je_type") != je_type:
                continue
            if variant is not None and row.get("je_type_variant") != variant:
                continue
            if exclude_period is not None and row.get("period") == exclude_period:
                continue
            if row.get("status") not in ("completed", "needs_review"):
                continue
            created_at = _parse_timestamp(row.get("created_at"))
            if created_at is None or not since <= created_at < before:
                continue
            total = _to_decimal(row.get("booked_total"))
            if total is None:
                total = _to_decimal(row.get("recomputed_total"))
            if total is not None:
                amounts.append(total)
        return amounts

    def list(
        self, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[list[IndexRow], str | None]:
        """Newest first. The cursor is the id to continue after."""
        with self._lock:
            index = self._read(INDEX_FILE)

        rows = sorted(index.values(), key=lambda row: str(row.get("created_at", "")), reverse=True)
        if cursor:
            ids = [str(row["review_id"]) for row in rows]
            start = ids.index(cursor) + 1 if cursor in ids else len(rows)
            rows = rows[start:]

        page = rows[:limit]
        next_cursor = str(page[-1]["review_id"]) if len(rows) > limit and page else None
        return page, next_cursor

    def _read(self, name: str) -> dict[str, IndexRow]:
        path = self._root / name
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("index %s is unreadable, continuing with an empty one: %s", name, exc)
            return {}
        return loaded if isinstance(loaded, dict) else {}


def _write_atomic(path: Path, payload: object) -> None:
    """Write via a temporary file and rename, so a reader never sees a partial record."""
    temp = path.with_suffix(path.suffix + _TEMP_SUFFIX)
    temp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def utc_now() -> datetime:
    """One clock for the whole harness, always timezone-aware."""
    return datetime.now(UTC)


def _decimal_str(value: Decimal | None) -> str | None:
    """How a total is written to the index. ``None`` stays ``None``, never ``"None"``."""
    return None if value is None else str(value)


def _to_decimal(value: object) -> Decimal | None:
    """A total read back off the index. Never raises on a row an older build wrote."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _parse_timestamp(value: object) -> datetime | None:
    """A ``created_at`` read back off the index. ``None`` for anything that is not one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
