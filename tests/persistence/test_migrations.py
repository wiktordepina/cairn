"""Tests for schema migration runner."""

from __future__ import annotations

import aiosqlite
import pytest

from cairn.persistence._errors import MigrationError
from cairn.persistence._migrations import (
    applied_versions,
    apply_pending,
    discover_migrations,
)


class TestDiscoverMigrations:
    def test_at_least_initial(self) -> None:
        migrations = discover_migrations()
        assert len(migrations) >= 1
        assert migrations[0].version == 1
        assert "schema_migrations" in migrations[0].sql

    def test_sorted_and_gap_free(self) -> None:
        migrations = discover_migrations()
        for expected, m in enumerate(migrations, start=1):
            assert m.version == expected


class TestAppliedVersions:
    @pytest.mark.asyncio
    async def test_empty_when_no_table(self) -> None:
        async with aiosqlite.connect(":memory:") as conn:
            assert await applied_versions(conn) == set()

    @pytest.mark.asyncio
    async def test_returns_applied_after_run(self) -> None:
        async with aiosqlite.connect(":memory:") as conn:
            await apply_pending(conn)
            applied = await applied_versions(conn)
            assert 1 in applied


class TestApplyPending:
    @pytest.mark.asyncio
    async def test_clean_apply(self) -> None:
        async with aiosqlite.connect(":memory:") as conn:
            applied = await apply_pending(conn)
            assert applied == [1]

    @pytest.mark.asyncio
    async def test_idempotent(self) -> None:
        async with aiosqlite.connect(":memory:") as conn:
            first = await apply_pending(conn)
            second = await apply_pending(conn)
            assert first == [1]
            assert second == []

    @pytest.mark.asyncio
    async def test_db_ahead_of_code_rejected(self) -> None:
        """If applied versions include one we don't know about, refuse to start."""
        async with aiosqlite.connect(":memory:") as conn:
            await apply_pending(conn)
            # Inject a fake "future" migration record
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (9999, "future", "2099-01-01T00:00:00+00:00"),
            )
            await conn.commit()
            with pytest.raises(MigrationError, match="ahead of code"):
                await apply_pending(conn)

    @pytest.mark.asyncio
    async def test_creates_expected_tables(self) -> None:
        async with aiosqlite.connect(":memory:") as conn:
            await apply_pending(conn)
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            await cursor.close()
            assert {
                "schema_migrations",
                "sessions",
                "messages",
                "tool_calls",
                "model_usage",
            } <= tables
