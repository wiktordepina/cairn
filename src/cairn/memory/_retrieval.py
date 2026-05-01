"""Memory retrieval — composite scoring on top of FTS5 BM25.

Implements the orchestrator's `MemoryService` protocol. For each
retrieval call:

1. Fetch up to `3*k` candidates from `MemoryRepo.search` (FTS5 BM25).
2. Rescore each with a composite of recency + importance + relevance.
3. Sort by composite score (higher is better) and truncate to `k`.
4. Truncate each entry's content to `retrieval_content_truncate`
   characters for bounded system-prompt size.

Formula (arch doc §4.11):

    score = 0.3 · recency + 0.3 · importance + 0.4 · relevance

    recency    = exp(-ln(2) · age_days / half_life)
    importance = (importance - 1) / 9            # 1..10 → 0..1
    relevance  = 1 / (1 + bm25_score)            # FTS5 bm25 is
                                                 # lower=better; invert

Half-lives by memory class:
- semantic → 90 days (facts, preferences, insights, relationships)
- episodic → 3 days  (events, tasks — decay fast so the retrieval
  layer naturally forgets them)
"""

from __future__ import annotations

import logging
import math
from datetime import UTC
from typing import TYPE_CHECKING

from cairn.domain._enums import MemoryClass

if TYPE_CHECKING:
    from datetime import datetime

    from cairn.config._models import MemoryConfig
    from cairn.domain._memory import MemoryEntry
    from cairn.orchestrator._clock import Clock
    from cairn.persistence._memory_repo import MemoryRepo
    from cairn.persistence._records import MemoryHit


log = logging.getLogger(__name__)


# Weights baked in per arch doc. Constants here rather than config so a
# weight tweak is a code change + test update, not a profile override —
# we don't want retrieval quality drifting per profile.
_RECENCY_WEIGHT = 0.3
_IMPORTANCE_WEIGHT = 0.3
_RELEVANCE_WEIGHT = 0.4

_HALF_LIFE_DAYS: dict[MemoryClass, float] = {
    MemoryClass.SEMANTIC: 90.0,
    MemoryClass.EPISODIC: 3.0,
}


def composite_score(
    *,
    entry: MemoryEntry,
    bm25_score: float,
    now: datetime,
) -> float:
    """Compute the composite retrieval score for one candidate.

    Pure function — tests pin it against golden values so changes to
    the formula are caught by drift, not by feel.
    """
    age_days = max((now - entry.updated_at).total_seconds() / 86_400.0, 0.0)
    half_life = _HALF_LIFE_DAYS[entry.memory_class]
    recency = math.exp(-math.log(2) * age_days / half_life)

    importance = max(0.0, min(1.0, (entry.importance - 1) / 9.0))

    relevance = 1.0 / (1.0 + bm25_score) if (1.0 + bm25_score) > 0 else 0.0

    return (
        _RECENCY_WEIGHT * recency + _IMPORTANCE_WEIGHT * importance + _RELEVANCE_WEIGHT * relevance
    )


class MemoryService:
    """Default `MemoryService` implementation (tier-1 only in V1).

    Thread the same instance everywhere memory is read — it holds no
    per-call state, just the injected collaborators.
    """

    def __init__(
        self,
        *,
        memory_repo: MemoryRepo,
        clock: Clock,
        memory_config: MemoryConfig,
    ) -> None:
        self._memory_repo = memory_repo
        self._clock = clock
        self._config = memory_config

    async def retrieve(
        self,
        *,
        space: str,
        query: str,
        k: int,
        truncate_content: bool = True,
    ) -> list[MemoryEntry]:
        """Return the top-*k* memories for *query* within *space*.

        An empty *space* (or one that yields no candidates) returns `[]`
        silently. No exceptions on miss — callers want to keep rendering
        their system prompt either way.

        The pre-turn preparer leaves *truncate_content* at its default
        (clipping per `MemoryConfig.retrieval_content_truncate` to keep
        the system prompt compact). User-facing surfaces (`/recall`
        modal, the `recall` tool) pass ``False`` to receive full
        entry bodies.
        """
        if not space or not query.strip() or k <= 0:
            return []

        candidates = await self._memory_repo.search(
            memory_space=space,
            query=query,
            k=max(k * 3, k),
        )
        if not candidates:
            return []

        now = self._clock.now().astimezone(UTC)
        scored: list[tuple[float, MemoryHit]] = [
            (composite_score(entry=hit.entry, bm25_score=hit.bm25_score, now=now), hit)
            for hit in candidates
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        kept = scored[:k]
        dropped = scored[k:]
        if dropped:
            log.debug(
                "memory retrieval dropped %d candidate(s) below top-%d for space=%s",
                len(dropped),
                k,
                space,
            )

        if not truncate_content:
            return [hit.entry for _, hit in kept]
        truncate = self._config.retrieval_content_truncate
        return [_truncate_content(hit.entry, truncate) for _, hit in kept]

    async def retrieve_scored(
        self,
        *,
        space: str,
        query: str,
        k: int,
    ) -> list[tuple[float, MemoryEntry]]:
        """Return the top-*k* memories paired with their composite score.

        Used by the `/recall` modal where we want to display the score
        column alongside the entries. Content is **not** truncated —
        the modal is the natural place to show full bodies on row
        expand.
        """
        if not space or not query.strip() or k <= 0:
            return []

        candidates = await self._memory_repo.search(
            memory_space=space,
            query=query,
            k=max(k * 3, k),
        )
        if not candidates:
            return []

        now = self._clock.now().astimezone(UTC)
        scored: list[tuple[float, MemoryHit]] = [
            (composite_score(entry=hit.entry, bm25_score=hit.bm25_score, now=now), hit)
            for hit in candidates
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        kept = scored[:k]
        return [(score, hit.entry) for score, hit in kept]


def _truncate_content(entry: MemoryEntry, limit: int) -> MemoryEntry:
    """Return *entry* with `content` truncated to *limit* chars.

    The original is frozen; this returns a fresh instance so the caller
    can mutate the returned list freely.
    """
    if limit <= 0 or len(entry.content) <= limit:
        return entry
    truncated = entry.content[:limit].rstrip() + "…"
    return entry.model_copy(update={"content": truncated})
