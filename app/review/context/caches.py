"""What the harness already knows: verified layouts and screenshots it has read before.

Both caches are keyed on every version that could change their value, so an entry is never
served across a rule, prompt or model change. The layout cache is the cost model: on a hit,
steps 8 and 11 do not run at all.

Storage is in memory here. The filesystem store behind it arrives with step 20; the
interface is the same either way, so nothing above has to change.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Final

from review.models.evidence import MethodologyReading, PictureReading
from review.models.plan import ExecutionPlan
from review.models.sheet_map import SheetMap

logger = logging.getLogger(__name__)

LAYOUT_TTL_DAYS: Final = 180
PICTURE_TTL_DAYS: Final = 180
METHODOLOGY_TTL_DAYS: Final = 180
_SECONDS_PER_DAY: Final = 86_400


@dataclass(slots=True)
class _Entry[ValueT]:
    value: ValueT
    created_at: float
    last_used_at: float
    hits: int = 0


class _ExpiringCache[ValueT]:
    """A small, thread-safe cache that forgets what has not been used.

    Step 13 runs its tools on a thread pool and several reviews run at once, so every read
    and write takes the lock. Entries expire on *idle* rather than age: a layout still in
    monthly use stays, and one whose template was retired falls out.
    """

    def __init__(self, ttl_days: int) -> None:
        self._ttl = ttl_days * _SECONDS_PER_DAY
        self._entries: dict[str, _Entry[ValueT]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> ValueT | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if now - entry.last_used_at > self._ttl:
                del self._entries[key]
                return None
            entry.last_used_at = now
            entry.hits += 1
            return entry.value

    def put(self, key: str, value: ValueT) -> None:
        now = time.time()
        with self._lock:
            self._entries[key] = _Entry(value=value, created_at=now, last_used_at=now)

    def evict_expired(self) -> int:
        """Drop idle entries. Called on a schedule, never on the request path."""
        now = time.time()
        with self._lock:
            stale = [k for k, e in self._entries.items() if now - e.last_used_at > self._ttl]
            for key in stale:
                del self._entries[key]
        return len(stale)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


@dataclass(frozen=True, slots=True)
class LayoutEntry:
    """A sheet map and plan that a completed review proved out."""

    sheet_map: SheetMap
    plan: ExecutionPlan
    je_type: str
    variant: str | None = field(default=None)


class LayoutCache:
    """Step 7. A verified map and plan for a workbook layout already seen.

    Only written after a review completes with **zero map problems**. A map that needed a
    repair is a map a person has not confirmed, and caching it would spread one bad reading
    across every future file from that template.
    """

    def __init__(self, ttl_days: int = LAYOUT_TTL_DAYS) -> None:
        self._cache: _ExpiringCache[LayoutEntry] = _ExpiringCache(ttl_days)

    @staticmethod
    def key(
        layout_fingerprint: str,
        *,
        je_type_version: str,
        understand_prompt: str,
        plan_prompt: str,
        model: str,
    ) -> str:
        """Everything that could change the map or the plan is in the key.

        The prompts are in it because a reworded instruction can produce a different map
        from the same workbook, and serving the old one would hide that.
        """
        material = "|".join(
            f"{len(part)}:{part}"
            for part in (
                layout_fingerprint,
                je_type_version,
                understand_prompt,
                plan_prompt,
                model,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> LayoutEntry | None:
        entry = self._cache.get(key)
        logger.info("layout cache %s key=%s", "hit" if entry else "miss", key[:12])
        return entry

    def put(self, key: str, entry: LayoutEntry, *, map_problems: int) -> bool:
        """Store only a clean map. Returns whether it was stored."""
        if map_problems:
            logger.info("layout not cached: %d map problems key=%s", map_problems, key[:12])
            return False
        self._cache.put(key, entry)
        return True

    def evict_expired(self) -> int:
        return self._cache.evict_expired()

    def __len__(self) -> int:
        return len(self._cache)


class MethodologyCache:
    """A methodology tab already read, keyed on its own words and the prompt that read them.

    Not keyed on the layout, deliberately. The same template arrives every month with a
    different methodology written on it, and that change is the finding — a layout-keyed
    cache would hide the one thing this reading exists to surface.
    """

    def __init__(self, ttl_days: int = METHODOLOGY_TTL_DAYS) -> None:
        self._cache: _ExpiringCache[MethodologyReading] = _ExpiringCache(ttl_days)

    @staticmethod
    def key(text_sha256: str, *, reader_prompt: str) -> str:
        return hashlib.sha256(
            f"{len(text_sha256)}:{text_sha256}|{len(reader_prompt)}:{reader_prompt}".encode()
        ).hexdigest()

    def get(self, key: str) -> MethodologyReading | None:
        return self._cache.get(key)

    def put(self, key: str, reading: MethodologyReading) -> None:
        self._cache.put(key, reading)

    def evict_expired(self) -> int:
        return self._cache.evict_expired()

    def __len__(self) -> int:
        return len(self._cache)


class PictureCache:
    """A screenshot already read, keyed by its bytes and the prompt that read it."""

    def __init__(self, ttl_days: int = PICTURE_TTL_DAYS) -> None:
        self._cache: _ExpiringCache[PictureReading] = _ExpiringCache(ttl_days)

    @staticmethod
    def key(picture_sha256: str, *, reader_prompt: str) -> str:
        return hashlib.sha256(
            f"{len(picture_sha256)}:{picture_sha256}|{len(reader_prompt)}:{reader_prompt}".encode()
        ).hexdigest()

    def get(self, key: str) -> PictureReading | None:
        return self._cache.get(key)

    def put(self, key: str, reading: PictureReading) -> None:
        self._cache.put(key, reading)

    def evict_expired(self) -> int:
        return self._cache.evict_expired()

    def __len__(self) -> int:
        return len(self._cache)
