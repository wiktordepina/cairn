"""Tests for SessionManager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.config._models import ModelConfig, ModelRole
from cairn.config._registry import ModelNotFoundError, ModelRegistry
from cairn.domain._enums import SessionType
from cairn.orchestrator import SessionManager, UnknownSession

if TYPE_CHECKING:
    from cairn.orchestrator._clock import Clock
    from cairn.persistence._sessions_repo import SessionRepo


def _model(id: str, *roles: ModelRole) -> ModelConfig:
    return ModelConfig(
        id=id,
        provider="anthropic",
        display_name=id,
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        input_cost_per_1m=1.0,
        output_cost_per_1m=1.0,
        roles=set(roles),
    )


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry(
        [
            _model("claude-opus-4-7", ModelRole.PRIMARY, ModelRole.REASONING),
            _model("claude-haiku-4-5", ModelRole.UTILITY, ModelRole.FAST),
        ]
    )


@pytest_asyncio.fixture
async def manager(
    session_repo: SessionRepo,
    registry: ModelRegistry,
    frozen_clock: Clock,
) -> SessionManager:
    return SessionManager(
        session_repo=session_repo,
        model_registry=registry,
        clock=frozen_clock,
    )


class TestCreate:
    @pytest.mark.asyncio
    async def test_creates_companion_session(self, manager: SessionManager) -> None:
        session = await manager.create(
            type=SessionType.COMPANION,
            persona="companion",
        )
        assert session.type is SessionType.COMPANION
        assert session.persona == "companion"
        assert session.memory_space == "companion"
        assert session.model == "claude-opus-4-7"
        assert session.parent_session_id is None
        assert not session.archived

    @pytest.mark.asyncio
    async def test_companion_respects_explicit_memory_space(
        self, manager: SessionManager
    ) -> None:
        session = await manager.create(
            type=SessionType.COMPANION,
            persona="companion",
            memory_space="work",
        )
        assert session.memory_space == "work"

    @pytest.mark.asyncio
    async def test_ephemeral_forces_no_memory_space(
        self, manager: SessionManager
    ) -> None:
        session = await manager.create(
            type=SessionType.EPHEMERAL,
            persona="_ephemeral",
            memory_space="tried-to-set-this",
        )
        assert session.memory_space is None

    @pytest.mark.asyncio
    async def test_persona_honours_memory_space(self, manager: SessionManager) -> None:
        with_space = await manager.create(
            type=SessionType.PERSONA,
            persona="work-assistant",
            memory_space="work",
        )
        without_space = await manager.create(
            type=SessionType.PERSONA,
            persona="one-off",
        )
        assert with_space.memory_space == "work"
        assert without_space.memory_space is None

    @pytest.mark.asyncio
    async def test_resolves_role_reference(self, manager: SessionManager) -> None:
        session = await manager.create(
            type=SessionType.EPHEMERAL,
            persona="x",
            model="role:utility",
        )
        assert session.model == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_resolves_literal_id(self, manager: SessionManager) -> None:
        session = await manager.create(
            type=SessionType.EPHEMERAL,
            persona="x",
            model="claude-haiku-4-5",
        )
        assert session.model == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_defaults_to_primary_role(self, manager: SessionManager) -> None:
        session = await manager.create(type=SessionType.EPHEMERAL, persona="x")
        assert session.model == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_unknown_model_propagates(self, manager: SessionManager) -> None:
        with pytest.raises(ModelNotFoundError):
            await manager.create(
                type=SessionType.EPHEMERAL, persona="x", model="no-such-model"
            )

    @pytest.mark.asyncio
    async def test_records_parent_session_id(
        self, manager: SessionManager
    ) -> None:
        parent = await manager.create(type=SessionType.COMPANION, persona="companion")
        child = await manager.create(
            type=SessionType.EPHEMERAL,
            persona="_delegation",
            parent_session_id=parent.id,
        )
        assert child.parent_session_id == parent.id

    @pytest.mark.asyncio
    async def test_timestamps_from_clock(
        self, manager: SessionManager, frozen_clock: Clock
    ) -> None:
        session = await manager.create(type=SessionType.COMPANION, persona="x")
        assert session.created_at == frozen_clock.now()
        assert session.updated_at == frozen_clock.now()

    @pytest.mark.asyncio
    async def test_ids_are_unique(self, manager: SessionManager) -> None:
        a = await manager.create(type=SessionType.EPHEMERAL, persona="x")
        b = await manager.create(type=SessionType.EPHEMERAL, persona="x")
        assert a.id != b.id

    @pytest.mark.asyncio
    async def test_persists_to_repo(
        self, manager: SessionManager, session_repo: SessionRepo
    ) -> None:
        session = await manager.create(type=SessionType.COMPANION, persona="x")
        fetched = await session_repo.get(session.id)
        assert fetched is not None
        assert fetched.id == session.id


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_existing(self, manager: SessionManager) -> None:
        created = await manager.create(type=SessionType.COMPANION, persona="x")
        got = await manager.get(created.id)
        assert got.id == created.id

    @pytest.mark.asyncio
    async def test_raises_unknown(self, manager: SessionManager) -> None:
        with pytest.raises(UnknownSession, match="no-such-id"):
            await manager.get("no-such-id")


class TestArchive:
    @pytest.mark.asyncio
    async def test_archives(self, manager: SessionManager) -> None:
        created = await manager.create(type=SessionType.COMPANION, persona="x")
        await manager.archive(created.id)
        got = await manager.get(created.id)
        assert got.archived

    @pytest.mark.asyncio
    async def test_raises_unknown(self, manager: SessionManager) -> None:
        with pytest.raises(UnknownSession):
            await manager.archive("no-such-id")
