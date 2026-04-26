"""Database connection management for the persistence layer."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aiosqlite

from cairn.persistence._migrations import apply_pending

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


_MEMORY_PATHS = {":memory:", "file::memory:"}


class Database:
    """A lazily-opened aiosqlite handle scoped to a single profile DB file.

    On first `connect()`:
    - Ensure parent directory exists with mode `0o700` (skipped for in-memory).
    - Open the connection with `aiosqlite.Row` factory.
    - Apply WAL/foreign-key/timeout PRAGMAs.
    - Run any pending schema migrations.

    Subsequent `connect()` calls reuse the same handle.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def connect(self) -> aiosqlite.Connection:
        """Return the open connection, lazily initialising if needed."""
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is None:
                if str(self._path) not in _MEMORY_PATHS:
                    self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                conn = await aiosqlite.connect(self._path)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode = WAL")
                await conn.execute("PRAGMA synchronous = NORMAL")
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 5000")
                await conn.execute("PRAGMA temp_store = MEMORY")
                await apply_pending(conn)
                self._conn = conn
        return self._conn

    async def close(self) -> None:
        """Close the connection if open. Idempotent."""
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection]:
        """Run a block inside `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`."""
        conn = await self.connect()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise
        else:
            await conn.commit()
