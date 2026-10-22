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
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from review.models.result import ReviewResult

IndexRow = dict[str, object]
"""One row of the listing index. Deliberately loose: it is a projection for display, and
pinning it to a model would make every stored index a migration."""

logger = logging.getLogger(__name__)

REVIEWS_DIR: Final = "reviews"
INDEX_FILE: Final = "index.json"
REUSE_FILE: Final = "reuse_index.json"
_TEMP_SUFFIX: Final = ".partial"


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
                "status": result.status,
                "score": result.score.total if result.score else None,
                "band": result.score.band if result.score else None,
                "provisional": result.score.provisional if result.score else False,
                "created_at": result.created_at.isoformat(),
            }
            _write_atomic(self._root / INDEX_FILE, index)

            # Only a review that actually completed is worth serving again. A failed or
            # cancelled one would hand back its own failure to the next identical upload.
            if reuse_key and result.status != "failed":
                reuse = self._reuse_index()
                reuse[reuse_key] = result.review_id
                _write_atomic(self._root / REUSE_FILE, reuse)

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
