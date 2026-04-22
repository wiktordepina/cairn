"""Append-only JSONL observation log.

Writes one line per observation to
`<memory_root>/observations/YYYY-MM-DD.jsonl`, rolling files at UTC
midnight. This is the disk-side mirror of `memory_entries` rows and
the thing a future multi-machine-sync rebuild path would replay.

V1 deliberately does *not* fsync per append — SQLite is the primary
durability store, and paying fsync on every observation would crush
throughput. Losing the last few lines on crash is acceptable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import MemoryClass, MemoryEntryType  # noqa: TC001

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.domain._memory import MemoryEntry


class Observation(BaseModel):
    """A single observation written to the JSONL log.

    Shape mirrors the `memory_entries` row so the rebuild tool (V3) can
    round-trip JSONL → SQLite without field-level translation. `id` is
    carried so dedup-merge events (same id seen twice with different
    `updated_at`) are recoverable.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    memory_space: str
    content: str
    entry_type: MemoryEntryType
    memory_class: MemoryClass
    importance: int
    source_session_id: str | None = None
    source_message_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entry(cls, entry: MemoryEntry) -> Observation:
        """Build an `Observation` from a persisted `MemoryEntry`."""
        if entry.id is None:
            raise ValueError("cannot log an Observation for an unpersisted entry")
        return cls(
            id=entry.id,
            memory_space=entry.memory_space,
            content=entry.content,
            entry_type=entry.entry_type,
            memory_class=entry.memory_class,
            importance=entry.importance,
            source_session_id=entry.source_session_id,
            source_message_id=entry.source_message_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )


class ObservationLog:
    """Append-only JSONL writer, one file per UTC date.

    Concurrent `append` calls are serialised through an `asyncio.Lock`
    so two coroutines can't interleave bytes on the same line.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._obs_dir = root / "observations"
        self._lock = asyncio.Lock()
        self._root_ready = False

    @property
    def root(self) -> Path:
        return self._root

    @property
    def observations_dir(self) -> Path:
        return self._obs_dir

    def path_for(self, when: datetime) -> Path:
        """Return the JSONL file path that *when* (in UTC) belongs to."""
        day = when.astimezone(UTC).strftime("%Y-%m-%d")
        return self._obs_dir / f"{day}.jsonl"

    async def append(
        self,
        observation: Observation,
        *,
        now: datetime | None = None,
    ) -> Path:
        """Append one JSON line for *observation*; return the file path
        it landed in.

        The *now* kwarg determines which day-file the line is routed to;
        default is `datetime.now(UTC)`. Explicit clocks keep tests
        deterministic.
        """
        now = now or datetime.now(UTC)
        line = observation.model_dump_json() + "\n"
        path = self.path_for(now)

        async with self._lock:
            if not self._root_ready:
                self._obs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._root_ready = True
            # Write inside the lock: concurrent writers on the same
            # day-file won't interleave partial lines. Small enough
            # payload that `write()` is effectively atomic, but the
            # lock makes the contract explicit.
            await asyncio.to_thread(_append_line, path, line)
        return path


def _append_line(path: Path, line: str) -> None:
    """Synchronously append *line* to *path* in binary mode."""
    with path.open("ab") as f:
        f.write(line.encode("utf-8"))
