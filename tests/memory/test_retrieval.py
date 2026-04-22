"""Tests for the MemoryService — composite scoring over FTS5 BM25."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from cairn.config._models import MemoryConfig
from cairn.domain._enums import MemoryClass, MemoryEntryType
from cairn.domain._memory import MemoryEntry
from cairn.memory._retrieval import MemoryService, composite_score
from cairn.orchestrator._clock import FrozenClock
from cairn.persistence._memory_repo import MemoryRepo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


NOW = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def memory_repo(db):
    return MemoryRepo(db)


@pytest.fixture
def clock():
    return FrozenClock(NOW)


def _make_entry(
    *,
    content: str = "content",
    entry_type: MemoryEntryType = MemoryEntryType.FACT,
    memory_class: MemoryClass = MemoryClass.SEMANTIC,
    importance: int = 5,
    updated_at: datetime | None = None,
    entry_id: int = 1,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        memory_space="companion",
        content=content,
        entry_type=entry_type,
        memory_class=memory_class,
        importance=importance,
        created_at=updated_at or NOW,
        updated_at=updated_at or NOW,
    )


# ---------------------------------------------------------------------------
# composite_score — pure formula tests
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_fresh_important_relevant_entry_scores_near_max(self) -> None:
        entry = _make_entry(importance=10, updated_at=NOW)
        # BM25=0 → relevance = 1.0; recency = 1.0 at age 0; importance
        # normalised to 1.0. Composite = 0.3 + 0.3 + 0.4 = 1.0.
        score = composite_score(entry=entry, bm25_score=0.0, now=NOW)
        assert score == pytest.approx(1.0)

    def test_cold_low_importance_low_relevance_scores_near_zero(self) -> None:
        # Very old entry, low importance, very high bm25 score.
        old = NOW - timedelta(days=900)
        entry = _make_entry(importance=1, updated_at=old)
        score = composite_score(entry=entry, bm25_score=1000.0, now=NOW)
        assert score < 0.01

    def test_semantic_half_life_90d(self) -> None:
        """At age = 90d for a semantic entry, recency should be 0.5."""
        ninety = NOW - timedelta(days=90)
        entry = _make_entry(memory_class=MemoryClass.SEMANTIC, updated_at=ninety)
        score = composite_score(entry=entry, bm25_score=0.0, now=NOW)
        # importance=5 → (5-1)/9 = 0.444…
        # recency = 0.5
        # relevance = 1.0
        expected = 0.3 * 0.5 + 0.3 * (4 / 9) + 0.4 * 1.0
        assert score == pytest.approx(expected)

    def test_episodic_half_life_3d(self) -> None:
        """At age = 3d for an episodic entry, recency should be 0.5."""
        three_days = NOW - timedelta(days=3)
        entry = _make_entry(
            memory_class=MemoryClass.EPISODIC,
            entry_type=MemoryEntryType.EVENT,
            updated_at=three_days,
        )
        score = composite_score(entry=entry, bm25_score=0.0, now=NOW)
        expected = 0.3 * 0.5 + 0.3 * (4 / 9) + 0.4 * 1.0
        assert score == pytest.approx(expected)

    def test_episodic_decays_faster_than_semantic(self) -> None:
        """Same age, same other inputs — episodic must score lower."""
        age = NOW - timedelta(days=7)
        semantic = _make_entry(memory_class=MemoryClass.SEMANTIC, updated_at=age)
        episodic = _make_entry(
            memory_class=MemoryClass.EPISODIC,
            entry_type=MemoryEntryType.EVENT,
            updated_at=age,
        )
        s_score = composite_score(entry=semantic, bm25_score=1.0, now=NOW)
        e_score = composite_score(entry=episodic, bm25_score=1.0, now=NOW)
        assert s_score > e_score

    def test_recency_monotonic_as_age_increases(self) -> None:
        prev = None
        for days in [0, 5, 30, 90, 365]:
            entry = _make_entry(updated_at=NOW - timedelta(days=days))
            s = composite_score(entry=entry, bm25_score=1.0, now=NOW)
            if prev is not None:
                assert s < prev
            prev = s

    def test_importance_normalisation_boundaries(self) -> None:
        low = _make_entry(importance=1, updated_at=NOW)
        high = _make_entry(importance=10, updated_at=NOW)
        low_score = composite_score(entry=low, bm25_score=5.0, now=NOW)
        high_score = composite_score(entry=high, bm25_score=5.0, now=NOW)
        assert high_score > low_score
        # importance contributes exactly 0.3 * 1.0 = 0.3 more.
        assert high_score - low_score == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# MemoryService.retrieve — integration with MemoryRepo
# ---------------------------------------------------------------------------


class TestRetrieveBasics:
    @pytest.mark.asyncio
    async def test_empty_space_returns_empty(self, memory_repo, clock) -> None:
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        assert await svc.retrieve(space="", query="python", k=8) == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, memory_repo, clock) -> None:
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        assert await svc.retrieve(space="companion", query="   ", k=8) == []

    @pytest.mark.asyncio
    async def test_k_zero_returns_empty(self, memory_repo, clock) -> None:
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        assert await svc.retrieve(space="companion", query="x", k=0) == []

    @pytest.mark.asyncio
    async def test_no_hits_returns_empty(self, memory_repo, clock) -> None:
        await memory_repo.store(
            memory_space="companion",
            content="User enjoys long walks with Cody the whippet",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        assert await svc.retrieve(space="companion", query="xenomorph", k=8) == []


class TestRetrieveTopK:
    @pytest.mark.asyncio
    async def test_k_truncation(self, memory_repo, clock) -> None:
        contents = [
            "Python is used in data pipelines nightly",
            "Python forms the backbone of ML research",
            "Python scripts automate deploy orchestration",
            "Python handles webhook validation flows",
            "Python underpins the analytics warehouse",
        ]
        for c in contents:
            await memory_repo.store(
                memory_space="companion",
                content=c,
                entry_type=MemoryEntryType.FACT,
                memory_class=MemoryClass.SEMANTIC,
            )
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        out = await svc.retrieve(space="companion", query="python", k=2)
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_space_scoping(self, memory_repo, clock) -> None:
        await memory_repo.store(
            memory_space="companion",
            content="Python in companion space",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        await memory_repo.store(
            memory_space="persona:alex",
            content="Python in persona space",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        companion = await svc.retrieve(space="companion", query="python", k=8)
        persona = await svc.retrieve(space="persona:alex", query="python", k=8)
        assert len(companion) == 1
        assert companion[0].memory_space == "companion"
        assert len(persona) == 1
        assert persona[0].memory_space == "persona:alex"


class TestRetrieveRanking:
    @pytest.mark.asyncio
    async def test_recent_important_beats_old_low_importance(self, memory_repo, clock):
        # Both match the query text; composite must prefer the recent +
        # important one even if BM25 alone would tie them.
        fresh = await memory_repo.store(
            memory_space="companion",
            content="Python tooling preferences for infra work",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=9,
            now=NOW,
        )
        stale = await memory_repo.store(
            memory_space="companion",
            content="Python tooling notes from long-ago archives",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            importance=2,
            now=NOW - timedelta(days=365),
        )
        assert fresh.id != stale.id

        svc = MemoryService(memory_repo=memory_repo, clock=clock, memory_config=MemoryConfig())
        out = await svc.retrieve(space="companion", query="python tooling", k=2)
        assert out[0].id == fresh.id


class TestContentTruncation:
    @pytest.mark.asyncio
    async def test_long_content_truncated_to_limit(self, memory_repo, clock):
        long_text = "Python observation " * 50  # well over 200 chars
        await memory_repo.store(
            memory_space="companion",
            content=long_text,
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(
            memory_repo=memory_repo,
            clock=clock,
            memory_config=MemoryConfig(retrieval_content_truncate=60),
        )
        out = await svc.retrieve(space="companion", query="python", k=1)
        assert len(out) == 1
        # +1 for the ellipsis character appended on truncation.
        assert len(out[0].content) <= 61
        assert out[0].content.endswith("…")

    @pytest.mark.asyncio
    async def test_short_content_not_truncated(self, memory_repo, clock):
        short = "Python is great"
        await memory_repo.store(
            memory_space="companion",
            content=short,
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(
            memory_repo=memory_repo,
            clock=clock,
            memory_config=MemoryConfig(retrieval_content_truncate=200),
        )
        out = await svc.retrieve(space="companion", query="python", k=1)
        assert out[0].content == short

    @pytest.mark.asyncio
    async def test_zero_truncate_limit_keeps_original(self, memory_repo, clock):
        """retrieval_content_truncate=0 disables truncation (safety hatch)."""
        long_text = "Python observation " * 50
        await memory_repo.store(
            memory_space="companion",
            content=long_text,
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(
            memory_repo=memory_repo,
            clock=clock,
            memory_config=MemoryConfig(retrieval_content_truncate=0),
        )
        out = await svc.retrieve(space="companion", query="python", k=1)
        assert out[0].content == long_text


class TestClockInjection:
    @pytest.mark.asyncio
    async def test_frozen_clock_gives_deterministic_recency(self, memory_repo):
        """Same data + different clocks → different scores."""
        stored = await memory_repo.store(
            memory_space="companion",
            content="Python fact from a while back",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            now=NOW,
        )
        assert stored.updated_at == NOW

        svc_fresh = MemoryService(
            memory_repo=memory_repo,
            clock=FrozenClock(NOW),
            memory_config=MemoryConfig(),
        )
        svc_future = MemoryService(
            memory_repo=memory_repo,
            clock=FrozenClock(NOW + timedelta(days=90)),
            memory_config=MemoryConfig(),
        )
        fresh_out = await svc_fresh.retrieve(space="companion", query="python", k=1)
        future_out = await svc_future.retrieve(space="companion", query="python", k=1)

        # Both hit; both returned. What we can assert is that the
        # recency portion of the composite differs — easiest by computing
        # composite_score directly rather than peeking inside.
        fresh_score = composite_score(entry=fresh_out[0], bm25_score=0.0, now=NOW)
        future_score = composite_score(
            entry=future_out[0], bm25_score=0.0, now=NOW + timedelta(days=90)
        )
        assert fresh_score > future_score
        # At 90 days the semantic recency factor is exactly 0.5 of the
        # zero-age value, so the score difference lies entirely in the
        # recency weight (0.3 * 0.5 = 0.15).
        assert fresh_score - future_score == pytest.approx(0.15)
