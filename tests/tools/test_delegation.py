"""Tests for ``DelegationTool``.

Wires a real ``SessionManager`` + ``BasicCostTracker`` over a tmp-path
SQLite DB, plugs ``FakeProvider`` into the ``ProviderRegistry`` via
``register_adapter``, and exercises the per-call flow from
``.plan/tool-system-design.md`` §12.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.config._models import (
    BudgetConfig,
    CairnConfig,
    DelegationToolConfig,
    ModelConfig,
    ModelRole,
    ProviderConfig,
)
from cairn.config._registry import ModelRegistry
from cairn.domain._enums import SessionType, StopReason, UsageOperation
from cairn.domain._provider import MessageStop, TextDelta, UsageEvent
from cairn.orchestrator import BasicCostTracker
from cairn.orchestrator._session_manager import SessionManager
from cairn.persistence import SessionRepo, UsageRepo
from cairn.providers._registry import ProviderRegistry
from cairn.tools._delegation import DelegationTool
from tests.orchestrator._fakes import FakeProvider

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import FrozenClock, TurnContext
    from cairn.persistence._connection import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def target_model() -> ModelConfig:
    # Pricey model so cost-cap tests trip on a modest token count.
    return ModelConfig(
        id="delegation-target",
        provider="fake",
        display_name="Delegation Target",
        context_window=50_000,
        max_output_tokens=2_048,
        supports_tools=False,
        input_cost_per_1m=10.0,
        output_cost_per_1m=30.0,
        # Claims PRIMARY so SessionManager.create can resolve
        # default_model_ref="role:primary" when building the parent session.
        roles={ModelRole.PRIMARY, ModelRole.UTILITY},
    )


@pytest.fixture
def model_registry(target_model: ModelConfig) -> ModelRegistry:
    return ModelRegistry([target_model])


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(name="fake")


@pytest.fixture
def provider_registry(provider: FakeProvider) -> ProviderRegistry:
    config = CairnConfig(
        schema_version=1,
        active_profile="test",
        providers={"fake": ProviderConfig(name="fake")},
        profiles={},
    )
    registry = ProviderRegistry(config=config, secret_resolver=None)  # type: ignore[arg-type]
    registry.register_adapter("fake", lambda *_: provider)
    return registry


@pytest_asyncio.fixture
async def session_manager(
    db: Database,
    model_registry: ModelRegistry,
    frozen_clock: FrozenClock,
) -> SessionManager:
    return SessionManager(
        session_repo=SessionRepo(db),
        model_registry=model_registry,
        clock=frozen_clock,
    )


@pytest_asyncio.fixture
async def cost_tracker(db: Database, frozen_clock: FrozenClock) -> BasicCostTracker:
    return BasicCostTracker(
        usage_repo=UsageRepo(db),
        clock=frozen_clock,
        budgets=BudgetConfig(per_turn_usd=100.0, per_session_usd=500.0, daily_usd=1000.0),
    )


def _config(
    *,
    name: str = "ask_utility",
    max_cost_usd: float | None = None,
    preserve_history: bool = False,
    sub_system_prompt: str | None = None,
) -> DelegationToolConfig:
    return DelegationToolConfig(
        tool_name=name,
        target_model="delegation-target",
        description="Consult the utility model.",
        when_to_use="For factual lookups.",
        preserve_history=preserve_history,
        sub_system_prompt=sub_system_prompt,
        max_cost_usd=max_cost_usd,
    )


def _make_tool(
    *,
    config: DelegationToolConfig,
    session_manager: SessionManager,
    provider_registry: ProviderRegistry,
    model_registry: ModelRegistry,
    cost_tracker: BasicCostTracker,
    clock: FrozenClock,
) -> DelegationTool:
    return DelegationTool(
        config=config,
        session_manager=session_manager,
        provider_registry=provider_registry,
        model_registry=model_registry,
        cost_tracker=cost_tracker,
        clock=clock,
    )


@pytest_asyncio.fixture
async def parent_session(session_manager: SessionManager) -> Session:
    return await session_manager.create(
        type=SessionType.COMPANION,
        persona="companion",
    )


def _script(provider: FakeProvider, *, text: str, input_tokens: int, output_tokens: int) -> None:
    """Append one scripted stream: deltas then usage + stop."""
    provider.scripted.append(
        [
            TextDelta(text=text),
            UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens),
            MessageStop(stop_reason=StopReason.END_TURN),
        ]
    )


# ---------------------------------------------------------------------------
# Basic invoke
# ---------------------------------------------------------------------------


class TestBasicInvoke:
    @pytest.mark.asyncio
    async def test_streams_accumulated_text(
        self,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="The answer is 42.", input_tokens=10, output_tokens=5)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        result = await tool.invoke(
            {"prompt": "What's the meaning of life?"},
            _ctx_with_session(turn_ctx, parent_session),
        )
        assert result.is_error is False
        assert result.content == "The answer is 42."

    @pytest.mark.asyncio
    async def test_returns_empty_tool_use_id(
        self,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="ok", input_tokens=1, output_tokens=1)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        result = await tool.invoke(
            {"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session)
        )
        # Tool itself leaves tool_use_id blank; the runner fills it in.
        assert result.tool_use_id == ""


# ---------------------------------------------------------------------------
# Sub-session
# ---------------------------------------------------------------------------


class TestSubSession:
    @pytest.mark.asyncio
    async def test_creates_ephemeral_sub_session_with_parent(
        self,
        db: Database,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="x", input_tokens=1, output_tokens=1)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        await tool.invoke({"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session))

        children = await SessionRepo(db).children_of(parent_session.id)
        assert len(children) == 1
        child = children[0]
        assert child.type is SessionType.EPHEMERAL
        assert child.parent_session_id == parent_session.id

    @pytest.mark.asyncio
    async def test_archives_sub_session_on_success(
        self,
        db: Database,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="done", input_tokens=1, output_tokens=1)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        await tool.invoke({"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session))
        children = await SessionRepo(db).children_of(parent_session.id)
        assert children[0].archived is True

    @pytest.mark.asyncio
    async def test_archives_sub_session_when_stream_raises(
        self,
        db: Database,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        # Empty scripted list → FakeProvider raises AssertionError on stream.
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        with pytest.raises(AssertionError):
            await tool.invoke(
                {"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session)
            )
        children = await SessionRepo(db).children_of(parent_session.id)
        assert children[0].archived is True


# ---------------------------------------------------------------------------
# Cost recording
# ---------------------------------------------------------------------------


class TestCostRecording:
    @pytest.mark.asyncio
    async def test_records_usage_with_delegation_operation(
        self,
        db: Database,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="x", input_tokens=100, output_tokens=50)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        await tool.invoke({"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session))

        rows = await UsageRepo(db).list_recent(session_id=None, limit=10)
        delegation_rows = [r for r in rows if r.operation is UsageOperation.DELEGATION]
        assert len(delegation_rows) == 1
        assert delegation_rows[0].input_tokens == 100
        assert delegation_rows[0].output_tokens == 50

    @pytest.mark.asyncio
    async def test_records_parent_session_id(
        self,
        db: Database,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="x", input_tokens=1, output_tokens=1)
        tool = _make_tool(
            config=_config(),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        await tool.invoke({"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session))
        rows = await UsageRepo(db).list_recent(session_id=None, limit=10)
        delegation_rows = [r for r in rows if r.operation is UsageOperation.DELEGATION]
        assert delegation_rows[0].parent_session_id == parent_session.id


# ---------------------------------------------------------------------------
# Cost cap
# ---------------------------------------------------------------------------


class TestCostCap:
    @pytest.mark.asyncio
    async def test_appends_truncation_note_when_cap_exceeded(
        self,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        # 1M input tokens at $10/M = $10 cost; cap at $1 → capped.
        _script(provider, text="partial", input_tokens=1_000_000, output_tokens=0)
        tool = _make_tool(
            config=_config(max_cost_usd=1.0),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        result = await tool.invoke(
            {"prompt": "long"}, _ctx_with_session(turn_ctx, parent_session)
        )
        assert isinstance(result.content, str)
        assert "cost cap reached" in result.content
        assert result.content.startswith("partial")

    @pytest.mark.asyncio
    async def test_no_cap_means_full_stream_consumed(
        self,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="full answer", input_tokens=1_000_000, output_tokens=1_000_000)
        tool = _make_tool(
            config=_config(max_cost_usd=None),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        result = await tool.invoke(
            {"prompt": "hi"}, _ctx_with_session(turn_ctx, parent_session)
        )
        assert result.content == "full answer"
        assert "cost cap" not in str(result.content)


# ---------------------------------------------------------------------------
# Preserve history guard
# ---------------------------------------------------------------------------


class TestPreserveHistory:
    def test_raises_on_preserve_history_true(
        self,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
    ) -> None:
        with pytest.raises(NotImplementedError, match="preserve_history"):
            _make_tool(
                config=_config(preserve_history=True),
                session_manager=session_manager,
                provider_registry=provider_registry,
                model_registry=model_registry,
                cost_tracker=cost_tracker,
                clock=frozen_clock,
            )


# ---------------------------------------------------------------------------
# Provider request wiring
# ---------------------------------------------------------------------------


class TestProviderRequest:
    @pytest.mark.asyncio
    async def test_uses_target_model_and_sub_system_prompt(
        self,
        provider: FakeProvider,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        parent_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _script(provider, text="x", input_tokens=1, output_tokens=1)
        tool = _make_tool(
            config=_config(sub_system_prompt="You are a pirate."),
            session_manager=session_manager,
            provider_registry=provider_registry,
            model_registry=model_registry,
            cost_tracker=cost_tracker,
            clock=frozen_clock,
        )
        await tool.invoke(
            {"prompt": "ahoy"}, _ctx_with_session(turn_ctx, parent_session)
        )
        assert len(provider.requests) == 1
        req = provider.requests[0]
        assert req.model == "delegation-target"
        assert req.system == "You are a pirate."
        # The single user message carries the prompt text.
        user_text = req.messages[0].get_text()
        assert user_text == "ahoy"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx_with_session(turn_ctx: TurnContext, session: Session) -> TurnContext:
    """Build a ``TurnContext`` pointing at ``session``.

    The shared ``turn_ctx`` fixture from ``tests/tools/conftest.py`` is
    tied to the shared companion session; delegation tests need the
    context bound to the freshly-inserted parent session so the
    sub-session's ``parent_session_id`` FK resolves.
    """
    from cairn.orchestrator._context import TurnContext as _TC

    return _TC(session=session, turn_id=turn_ctx.turn_id, iteration=turn_ctx.iteration)
