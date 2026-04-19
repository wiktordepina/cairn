"""Tests for the Database connection wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from cairn.persistence._connection import Database


class TestDatabase:
    @pytest.mark.asyncio
    async def test_lazy_connect(self, db: Database) -> None:
        assert db._conn is None  # noqa: SLF001
        await db.connect()
        assert db._conn is not None  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, db: Database) -> None:
        conn = await db.connect()
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        assert row[0].lower() == "wal"

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self, db: Database) -> None:
        conn = await db.connect()
        cursor = await conn.execute("PRAGMA foreign_keys")
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_migrations_applied_on_connect(self, db: Database) -> None:
        conn = await db.connect()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None

    @pytest.mark.asyncio
    async def test_second_connect_reuses_handle(self, db: Database) -> None:
        a = await db.connect()
        b = await db.connect()
        assert a is b

    @pytest.mark.asyncio
    async def test_close_idempotent(self, db: Database) -> None:
        await db.connect()
        await db.close()
        await db.close()  # should not raise

    @pytest.mark.asyncio
    async def test_transaction_commits(self, db: Database) -> None:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO sessions (id, type, persona, model, memory_space, "
                "archived, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("tx-1", "companion", "companion", "x", None, 0, "2026-01-01", "2026-01-01"),
            )
        # Verify committed
        conn = await db.connect()
        cursor = await conn.execute("SELECT id FROM sessions WHERE id = ?", ("tx-1",))
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None

    @pytest.mark.asyncio
    async def test_transaction_rolls_back(self, db: Database) -> None:
        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO sessions (id, type, persona, model, memory_space, "
                    "archived, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "tx-rb",
                        "companion",
                        "companion",
                        "x",
                        None,
                        0,
                        "2026-01-01",
                        "2026-01-01",
                    ),
                )
                raise Boom
        # Verify rolled back
        conn = await db.connect()
        cursor = await conn.execute("SELECT id FROM sessions WHERE id = ?", ("tx-rb",))
        row = await cursor.fetchone()
        await cursor.close()
        assert row is None
