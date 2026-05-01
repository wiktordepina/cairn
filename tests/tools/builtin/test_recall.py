"""Tests for the `recall` built-in tool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from cairn.config._models import MemoryConfig
from cairn.domain._enums import MemoryClass, MemoryEntryType
from cairn.memory._retrieval import MemoryService
from cairn.persistence._memory_repo import MemoryRepo
from cairn.tools.builtin._recall import RecallArgs, make_recall

if TYPE_CHECKING:
    from cairn.domain import Session
    from cairn.orchestrator import FrozenClock, TurnContext
    from cairn.persistence._connection import Database


def _service(db: Database, frozen_clock: FrozenClock) -> MemoryService:
    return MemoryService(
        memory_repo=MemoryRepo(db),
        clock=frozen_clock,
        memory_config=MemoryConfig(),
    )


def _space_from_session(ctx: TurnContext) -> str:
    return ctx.session.memory_space or ""


class TestMetadata:
    def test_tier_zero_no_approval(self, db: Database, frozen_clock: FrozenClock) -> None:
        t = make_recall(_service(db, frozen_clock), memory_space_provider=_space_from_session)
        assert t.name == "recall"
        assert t.risk_tier == 0
        assert t.side_effects == "none"
        assert t.approval_required is False


class TestArgsValidation:
    def test_k_default_is_eight(self) -> None:
        args = RecallArgs(query="anything")
        assert args.k == 8

    def test_k_lower_bound(self) -> None:
        with pytest.raises(ValueError):
            RecallArgs(query="x", k=0)

    def test_k_upper_bound(self) -> None:
        with pytest.raises(ValueError):
            RecallArgs(query="x", k=21)

    def test_query_required(self) -> None:
        with pytest.raises(ValueError):
            RecallArgs()  # type: ignore[call-arg]


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_full_content_and_metadata(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        turn_ctx: TurnContext,
    ) -> None:
        repo = MemoryRepo(db)
        long_text = "Wiktor takes coffee with no sugar, oat milk, single shot. " * 6
        await repo.store(
            memory_space="companion",
            content=long_text,
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=8,
        )
        svc = MemoryService(memory_repo=repo, clock=frozen_clock, memory_config=MemoryConfig())
        t = make_recall(svc, memory_space_provider=_space_from_session)

        result = await t.invoke({"query": "coffee"}, turn_ctx)
        payload = json.loads(result.content)  # type: ignore[arg-type]
        assert "hits" in payload
        assert len(payload["hits"]) == 1
        hit = payload["hits"][0]
        # Full content — not truncated.
        assert hit["content"] == long_text
        assert hit["type"] == "preference"
        assert hit["importance"] == 8
        assert "id" in hit
        assert "created_at" in hit


class TestEmptyResults:
    @pytest.mark.asyncio
    async def test_no_matches_returns_note(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_recall(_service(db, frozen_clock), memory_space_provider=_space_from_session)
        result = await t.invoke({"query": "nothing-stored-yet"}, turn_ctx)
        payload = json.loads(result.content)  # type: ignore[arg-type]
        assert payload == {"hits": [], "note": "no matches"}

    @pytest.mark.asyncio
    async def test_session_without_memory_space_returns_note(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        persona_session: Session,
    ) -> None:
        from cairn.orchestrator import TurnContext as _TC

        ctx = _TC(session=persona_session, turn_id="t-x", iteration=0)
        t = make_recall(_service(db, frozen_clock), memory_space_provider=_space_from_session)
        result = await t.invoke({"query": "anything"}, ctx)
        payload = json.loads(result.content)  # type: ignore[arg-type]
        assert payload == {"hits": [], "note": "session has no memory_space"}


class TestKBounds:
    @pytest.mark.asyncio
    async def test_runner_rejects_k_above_twenty(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        turn_ctx: TurnContext,
    ) -> None:
        from pydantic import ValidationError

        t = make_recall(_service(db, frozen_clock), memory_space_provider=_space_from_session)
        with pytest.raises(ValidationError):
            await t.invoke({"query": "x", "k": 50}, turn_ctx)

    @pytest.mark.asyncio
    async def test_runner_rejects_k_zero(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        turn_ctx: TurnContext,
    ) -> None:
        from pydantic import ValidationError

        t = make_recall(_service(db, frozen_clock), memory_space_provider=_space_from_session)
        with pytest.raises(ValidationError):
            await t.invoke({"query": "x", "k": 0}, turn_ctx)


class TestMemorySpaceProvider:
    @pytest.mark.asyncio
    async def test_provider_reads_session_at_call_time(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        turn_ctx: TurnContext,
    ) -> None:
        # The provider is called per-invocation; we should see the
        # session's memory_space, not whatever was bound at factory
        # construction.
        repo = MemoryRepo(db)
        await repo.store(
            memory_space="companion",
            content="Coffee preferences",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
        )
        svc = MemoryService(memory_repo=repo, clock=frozen_clock, memory_config=MemoryConfig())

        captured: list[str] = []

        def _provider(ctx: TurnContext) -> str:
            space = ctx.session.memory_space or ""
            captured.append(space)
            return space

        t = make_recall(svc, memory_space_provider=_provider)
        await t.invoke({"query": "coffee"}, turn_ctx)
        assert captured == ["companion"]
