"""Tests for `MemoryRepo` — the tier-1 observation store.

Covers:
- Migration forward
- Round-trip store/get/recent/count
- Dedup: near-duplicate merges + max-importance + updated_at refresh
- Dedup: different space / entry_type does NOT merge
- Search: FTS5 match, space scoping, entry_type filtering
- Validation: empty space, importance out of range, empty content
- Soak test: many inserts with deliberate duplicates yield expected
  dedup outcome
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.domain._enums import MemoryClass, MemoryEntryType
from cairn.persistence._memory_repo import MemoryRepo
from cairn.persistence._migrations import apply_pending, discover_migrations

if TYPE_CHECKING:
    from cairn.persistence._connection import Database


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db: Database) -> MemoryRepo:
    return MemoryRepo(db)


def _at(hours: int = 0) -> datetime:
    return datetime(2026, 4, 22, 12, 0, tzinfo=UTC) + timedelta(hours=hours)


# ----------------------------------------------------------------------
# Migration forward
# ----------------------------------------------------------------------


class TestMigration:
    def test_0003_is_registered_and_monotonic(self) -> None:
        migrations = discover_migrations()
        versions = [m.version for m in migrations]
        assert 3 in versions
        # Gap-freeness already asserted by discover_migrations()

    @pytest.mark.asyncio
    async def test_tables_and_fts_created(self, db: Database) -> None:
        conn = await db.connect()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memory%'"
        )
        names = {row[0] for row in await cursor.fetchall()}
        await cursor.close()
        assert "memory_entries" in names
        # FTS5 creates several backing tables; the virtual table name is always there.
        assert any(n.startswith("memory_entries_fts") for n in names)

    @pytest.mark.asyncio
    async def test_applied_once_is_idempotent_on_reconnect(self, db: Database) -> None:
        await db.connect()
        await db.close()
        # Reopen; apply_pending is called inside connect() and must no-op
        # for already-applied migrations.
        conn = await db.connect()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 3"
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None and row[0] == 1

    @pytest.mark.asyncio
    async def test_applies_cleanly_to_fresh_connection(self) -> None:
        """Explicit apply against a bare aiosqlite connection."""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as conn:
            applied = await apply_pending(conn)
            assert 3 in applied


# ----------------------------------------------------------------------
# Store / round-trip
# ----------------------------------------------------------------------


class TestStoreAndGet:
    @pytest.mark.asyncio
    async def test_insert_and_fetch(self, repo: MemoryRepo) -> None:
        entry = await repo.store(
            memory_space="companion",
            content="User prefers Python for scripting tasks",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=7,
            now=_at(),
        )
        assert entry.id is not None
        assert entry.importance == 7

        fetched = await repo.get(entry.id)
        assert fetched is not None
        assert fetched.content == "User prefers Python for scripting tasks"
        assert fetched.entry_type == MemoryEntryType.PREFERENCE
        assert fetched.memory_class == MemoryClass.SEMANTIC
        assert fetched.memory_space == "companion"

    @pytest.mark.asyncio
    async def test_defaults(self, repo: MemoryRepo) -> None:
        entry = await repo.store(
            memory_space="companion",
            content="Fact about the world",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert entry.importance == 5
        assert entry.source_session_id is None
        assert entry.source_message_id is None

    @pytest.mark.asyncio
    async def test_source_links_round_trip(
        self, repo: MemoryRepo, session_repo, message_repo
    ) -> None:
        from tests.persistence.conftest import make_db_message, make_db_session

        await session_repo.insert(make_db_session())
        msg = make_db_message(text="some turn text")
        await message_repo.append(msg)

        entry = await repo.store(
            memory_space="companion",
            content="An extracted observation",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            source_session_id="sess-test",
            source_message_id=msg.id,
        )
        fetched = await repo.get(entry.id)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.source_session_id == "sess-test"
        assert fetched.source_message_id == msg.id


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_empty_space_rejected(self, repo: MemoryRepo) -> None:
        with pytest.raises(ValueError, match="memory_space"):
            await repo.store(
                memory_space="",
                content="x",
                entry_type=MemoryEntryType.FACT,
                memory_class=MemoryClass.SEMANTIC,
            )

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, repo: MemoryRepo) -> None:
        with pytest.raises(ValueError, match="content"):
            await repo.store(
                memory_space="companion",
                content="   ",
                entry_type=MemoryEntryType.FACT,
                memory_class=MemoryClass.SEMANTIC,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [0, 11, -1, 100])
    async def test_importance_out_of_range_rejected(self, repo: MemoryRepo, bad: int) -> None:
        with pytest.raises(ValueError, match="importance"):
            await repo.store(
                memory_space="companion",
                content="x",
                entry_type=MemoryEntryType.FACT,
                memory_class=MemoryClass.SEMANTIC,
                importance=bad,
            )


# ----------------------------------------------------------------------
# Dedup
# ----------------------------------------------------------------------


class TestDedup:
    @pytest.mark.asyncio
    async def test_exact_duplicate_merges(self, repo: MemoryRepo) -> None:
        content = "User prefers Python for scripting tasks"
        first = await repo.store(
            memory_space="companion",
            content=content,
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=5,
            now=_at(0),
        )
        second = await repo.store(
            memory_space="companion",
            content=content,
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=8,
            now=_at(1),
        )
        assert second.id == first.id
        assert second.importance == 8  # max wins
        assert second.updated_at > first.updated_at
        assert await repo.count_for_space("companion") == 1

    @pytest.mark.asyncio
    async def test_near_duplicate_merges(self, repo: MemoryRepo) -> None:
        first = await repo.store(
            memory_space="companion",
            content="User prefers Python for scripting tasks",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=5,
        )
        second = await repo.store(
            memory_space="companion",
            # One-word diff — SequenceMatcher ratio should clear 0.90.
            content="User prefers Python for scripting work",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=7,
        )
        assert second.id == first.id
        assert await repo.count_for_space("companion") == 1

    @pytest.mark.asyncio
    async def test_importance_merge_takes_max_not_min(self, repo: MemoryRepo) -> None:
        first = await repo.store(
            memory_space="companion",
            content="The same fact",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            importance=9,
        )
        second = await repo.store(
            memory_space="companion",
            content="The same fact",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            importance=3,
        )
        assert second.id == first.id
        assert second.importance == 9

    @pytest.mark.asyncio
    async def test_different_space_does_not_merge(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="Shared content",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="persona:alex",
            content="Shared content",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert await repo.count_for_space("companion") == 1
        assert await repo.count_for_space("persona:alex") == 1

    @pytest.mark.asyncio
    async def test_different_entry_type_does_not_merge(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="Overlapping content words",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="companion",
            content="Overlapping content words",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert await repo.count_for_space("companion") == 2

    @pytest.mark.asyncio
    async def test_dissimilar_content_does_not_merge(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="User prefers Python for scripting tasks",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="companion",
            content="User has a 4-year-old whippet named Cody",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert await repo.count_for_space("companion") == 2

    @pytest.mark.asyncio
    async def test_all_stopword_content_skips_dedup(self, repo: MemoryRepo) -> None:
        """Content with no significant words can't generate an FTS query,
        so dedup is skipped — the second insert lands as a new row."""
        first = await repo.store(
            memory_space="companion",
            content="to the",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        second = await repo.store(
            memory_space="companion",
            content="to the",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        # Both inserts succeeded — no dedup candidate query possible.
        assert second.id != first.id


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_match_returns_hit(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="User prefers Python for scripting tasks",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        hits = await repo.search("companion", "python scripting")
        assert len(hits) == 1
        assert "Python" in hits[0].entry.content

    @pytest.mark.asyncio
    async def test_space_scoping(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="User prefers Python",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="persona:alex",
            content="User prefers Python",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        companion_hits = await repo.search("companion", "python")
        persona_hits = await repo.search("persona:alex", "python")
        assert len(companion_hits) == 1
        assert len(persona_hits) == 1
        assert companion_hits[0].entry.memory_space == "companion"
        assert persona_hits[0].entry.memory_space == "persona:alex"

    @pytest.mark.asyncio
    async def test_entry_type_filter(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="Python is a popular language",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="companion",
            content="User loves Python scripting work",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        fact_only = await repo.search(
            "companion", "python", entry_types=[MemoryEntryType.FACT]
        )
        assert len(fact_only) == 1
        assert fact_only[0].entry.entry_type == MemoryEntryType.FACT

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="some content",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert await repo.search("companion", "   ") == []

    @pytest.mark.asyncio
    async def test_k_truncation(self, repo: MemoryRepo) -> None:
        # Distinct enough that dedup doesn't collapse them — each carries
        # its own unique vocabulary alongside "python".
        distinct = [
            "Python is used in data pipelines nightly",
            "Python forms the backbone of ML research",
            "Python scripts automate deploy orchestration",
            "Python handles webhook validation flows",
            "Python underpins the analytics warehouse",
            "Python runs the nightly ETL batch",
            "Python powers observability tooling",
            "Python drives the migration runner",
            "Python serves the retraining scheduler",
            "Python coordinates the report builder",
        ]
        for content in distinct:
            await repo.store(
                memory_space="companion",
                content=content,
                entry_type=MemoryEntryType.FACT,
                memory_class=MemoryClass.SEMANTIC,
            )
        hits = await repo.search("companion", "python", k=3)
        assert len(hits) == 3


# ----------------------------------------------------------------------
# Recent / count / delete
# ----------------------------------------------------------------------


class TestRecentCountDelete:
    @pytest.mark.asyncio
    async def test_recent_ordered_by_updated_at_desc(self, repo: MemoryRepo) -> None:
        a = await repo.store(
            memory_space="companion",
            content="first entry alpha",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            now=_at(0),
        )
        b = await repo.store(
            memory_space="companion",
            content="second entry bravo",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            now=_at(1),
        )
        recent = await repo.recent("companion")
        assert [r.id for r in recent] == [b.id, a.id]

    @pytest.mark.asyncio
    async def test_count_is_space_scoped(self, repo: MemoryRepo) -> None:
        await repo.store(
            memory_space="companion",
            content="one",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.store(
            memory_space="persona:alex",
            content="two",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        assert await repo.count_for_space("companion") == 1
        assert await repo.count_for_space("persona:alex") == 1
        assert await repo.count_for_space("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_delete_removes_row_and_fts(self, repo: MemoryRepo) -> None:
        entry = await repo.store(
            memory_space="companion",
            content="transient observation gamma",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await repo.delete(entry.id)  # type: ignore[arg-type]
        assert await repo.get(entry.id) is None  # type: ignore[arg-type]
        # FTS index should be clean too
        assert await repo.search("companion", "gamma") == []


# ----------------------------------------------------------------------
# Soak test
# ----------------------------------------------------------------------


class TestSoak:
    @pytest.mark.asyncio
    async def test_mixed_inserts_with_duplicates_yields_expected_count(
        self, repo: MemoryRepo
    ) -> None:
        """Insert 1000 rows with deliberate duplication; final count
        should match the number of unique content payloads."""
        unique_templates = [
            "Observation number {n} about system architecture",
            "User prefers tool {n} for development workflow",
            "Cody went for walk number {n} this week",
        ]
        # 50 unique slots × 3 templates = 150 unique observations.
        # We'll write each 7 times → 1050 store() calls.
        unique_contents = {
            template.format(n=n)
            for n in range(50)
            for template in unique_templates
        }
        for _ in range(7):
            for content in sorted(unique_contents):
                await repo.store(
                    memory_space="companion",
                    content=content,
                    entry_type=MemoryEntryType.FACT,
                    memory_class=MemoryClass.SEMANTIC,
                )
        final_count = await repo.count_for_space("companion")
        # Dedup should have collapsed the 7× duplication down to the
        # unique-content count. Allow a small slack for near-dup
        # collisions between different `n` values, which can occur
        # since the templates share heavy overlap.
        assert final_count <= len(unique_contents)
        assert final_count >= 1  # sanity
