"""Tests for the Orchestrator turn loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.config._models import (
    BudgetConfig,
    CairnConfig,
    ModelConfig,
    ModelRole,
    ProviderConfig,
)
from cairn.config._registry import ModelRegistry
from cairn.domain._content import TextBlock
from cairn.domain._enums import SessionType, StopReason, UsageOperation
from cairn.domain._events import (
    AssistantTextDelta,
    ObservationExtractionRequested,
    SessionCreated,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
)
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.orchestrator import (
    AutoApproveGateway,
    BasicCostTracker,
    DenyAllGateway,
    FrozenClock,
    MinimalContextManager,
    NullExtractionQueue,
    NullMemoryService,
    Orchestrator,
    OrchestratorConfig,
    RaisingToolRunner,
    SessionManager,
    TurnAlreadyRunning,
    TurnState,
)
from cairn.providers._registry import ProviderRegistry

from ._fakes import (
    DictToolRegistry,
    EventCollector,
    FakeProvider,
    RecordingToolRunner,
    SpyExtractionQueue,
    SpyMemoryService,
    StubTool,
)

if TYPE_CHECKING:
    from cairn.persistence._connection import Database
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._sessions_repo import SessionRepo
    from cairn.persistence._usage_repo import UsageRepo


# ---------------------------------------------------------------------------
# Fixtures that wire a complete orchestrator.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def message_repo(db: Database):
    from cairn.persistence._messages_repo import MessageRepo

    return MessageRepo(db)


@pytest_asyncio.fixture
async def turn_repo(db: Database):
    from cairn.persistence._turns_repo import TurnRepo

    return TurnRepo(db)


@pytest_asyncio.fixture
async def usage_repo(db: Database):
    from cairn.persistence._usage_repo import UsageRepo

    return UsageRepo(db)


@pytest.fixture
def model_registry() -> ModelRegistry:
    return ModelRegistry(
        [
            ModelConfig(
                id="test-primary",
                provider="fake",
                display_name="Test Primary",
                context_window=100_000,
                max_output_tokens=4_096,
                supports_tools=True,
                input_cost_per_1m=1.0,
                output_cost_per_1m=2.0,
                roles={ModelRole.PRIMARY},
            )
        ]
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(name="fake")


@pytest.fixture
def provider_registry(provider: FakeProvider) -> ProviderRegistry:
    # Build a minimal CairnConfig so ProviderRegistry's by_name can find
    # a provider_config for the "fake" name, then register a factory
    # that ignores inputs and returns our FakeProvider instance.
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
    session_repo: SessionRepo,
    model_registry: ModelRegistry,
    frozen_clock: FrozenClock,
) -> SessionManager:
    return SessionManager(
        session_repo=session_repo,
        model_registry=model_registry,
        clock=frozen_clock,
    )


@pytest.fixture
def collector() -> EventCollector:
    return EventCollector()


@pytest.fixture
def budgets() -> BudgetConfig:
    return BudgetConfig(per_turn_usd=0.50, per_session_usd=5.00, daily_usd=20.00)


@pytest.fixture
def orchestrator_config() -> OrchestratorConfig:
    return OrchestratorConfig(max_iterations=5)


@pytest_asyncio.fixture
async def cost_tracker(
    usage_repo: UsageRepo, frozen_clock: FrozenClock, budgets: BudgetConfig
) -> BasicCostTracker:
    return BasicCostTracker(usage_repo=usage_repo, clock=frozen_clock, budgets=budgets)


def _make_orchestrator(
    *,
    provider_registry: ProviderRegistry,
    model_registry: ModelRegistry,
    session_manager: SessionManager,
    message_repo,
    turn_repo,
    cost_tracker,
    frozen_clock: FrozenClock,
    orchestrator_config: OrchestratorConfig,
    collector: EventCollector,
    tool_registry=None,
    tool_runner=None,
    memory_service=None,
    extraction_queue=None,
    approvers=(),
    approval_gateway=None,
) -> Orchestrator:
    return Orchestrator(
        provider_registry=provider_registry,
        model_registry=model_registry,
        session_manager=session_manager,
        context_manager=MinimalContextManager(system_prompt="you are cairn"),
        tool_registry=tool_registry or DictToolRegistry(),
        tool_runner=tool_runner or RaisingToolRunner(),
        memory_service=memory_service or NullMemoryService(),
        extraction_queue=extraction_queue or NullExtractionQueue(),
        approval_gateway=approval_gateway or AutoApproveGateway(),
        cost_tracker=cost_tracker,
        turn_repo=turn_repo,
        message_repo=message_repo,
        clock=frozen_clock,
        config=orchestrator_config,
        approvers=approvers,
        observers=[collector],
    )


def _user(text: str) -> Message:
    msg = Message(role="user")
    msg.content.append(TextBlock(text=text))
    return msg


# ---------------------------------------------------------------------------
# Happy path — no tools
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_text_only_turn(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="Hello"),
                TextDelta(text=" there."),
                UsageEvent(input_tokens=10, output_tokens=5),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")

        events = []
        async for ev in orch.run_turn(session.id, _user("Hi")):
            events.append(ev)

        types = [type(e).__name__ for e in events]
        assert types == [
            "UserMessagePersisted",
            "AssistantTextDelta",
            "AssistantTextDelta",
            "AssistantMessageComplete",
            "ObservationExtractionRequested",
            "TurnComplete",
        ]
        # Reconstruct the assistant's text.
        text = "".join(e.text for e in events if isinstance(e, AssistantTextDelta))
        assert text == "Hello there."

        # Final event carries the stop reason.
        final = [e for e in events if isinstance(e, TurnComplete)]
        assert final and final[0].stop_reason is StopReason.END_TURN

    @pytest.mark.asyncio
    async def test_persists_user_and_assistant_messages(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        async for _ in orch.run_turn(session.id, _user("hi")):
            pass

        msgs = await message_repo.list_for_session(session.id)
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].get_text() == "hi"
        assert msgs[1].get_text() == "ok"

    @pytest.mark.asyncio
    async def test_turn_row_reaches_completed(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        turn_complete = None
        async for ev in orch.run_turn(session.id, _user("hi")):
            if isinstance(ev, TurnComplete):
                turn_complete = ev
        assert turn_complete is not None
        turn = await turn_repo.get(turn_complete.turn_id)
        assert turn is not None
        assert turn.state is TurnState.COMPLETED
        assert turn.stop_reason is StopReason.END_TURN

    @pytest.mark.asyncio
    async def test_usage_recorded_with_turn_id(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        usage_repo: UsageRepo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="x"),
                UsageEvent(input_tokens=100, output_tokens=50),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        turn_id = None
        async for ev in orch.run_turn(session.id, _user("hi")):
            if isinstance(ev, TurnComplete):
                turn_id = ev.turn_id
        assert turn_id is not None

        rows = await usage_repo.list_recent(session_id=session.id)
        assert len(rows) == 1
        assert rows[0].turn_id == turn_id
        # 100 * 1.0/1M + 50 * 2.0/1M = 0.0001 + 0.0001 = 0.0002
        assert rows[0].cost_usd == pytest.approx(0.0002)
        assert rows[0].operation is UsageOperation.PRIMARY_TURN


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_full_tool_flow(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        # First iteration: tool call. Second: text response.
        provider.scripted = [
            [
                ToolCallStart(id="tc-1", name="echo"),
                ToolCallEnd(id="tc-1"),
                UsageEvent(input_tokens=5, output_tokens=3),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="done"),
                UsageEvent(input_tokens=2, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ],
        ]

        async def echo_handler(tool_call, session, turn_id):  # noqa: ARG001
            return ("echoed", False)

        runner = RecordingToolRunner(handlers={"echo": echo_handler})
        registry = DictToolRegistry(tools={"echo": StubTool(name="echo", approval_required=False)})
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            tool_registry=registry,
            tool_runner=runner,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        async for ev in orch.run_turn(session.id, _user("run echo")):
            events.append(ev)

        names = [type(e).__name__ for e in events]
        # Expected sequence covers the full tool lifecycle.
        assert "ToolCallPlanned" in names
        assert "ToolCallApproved" in names
        assert "ToolCallStarted" in names
        assert "ToolCallCompleted" in names
        assert "TurnComplete" in names
        # Tool runner was called once.
        assert len(runner.calls) == 1
        assert runner.calls[0]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_rejected_tool_flows_error_to_model(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                ToolCallStart(id="tc-1", name="writer"),
                ToolCallEnd(id="tc-1"),
                UsageEvent(input_tokens=5, output_tokens=3),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="gave up"),
                UsageEvent(input_tokens=2, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ],
        ]

        async def should_not_reach_handler(*args, **kwargs):
            raise AssertionError("tool handler must not be invoked for a rejected tool")

        runner = RecordingToolRunner(handlers={"writer": should_not_reach_handler})
        registry = DictToolRegistry(
            tools={"writer": StubTool(name="writer", approval_required=True, side_effects="write")}
        )
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            tool_registry=registry,
            tool_runner=runner,
            approval_gateway=DenyAllGateway(),
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        async for ev in orch.run_turn(session.id, _user("do thing")):
            events.append(ev)

        # Rejected, not approved. Runner IS called (to write the audit
        # row + synthesise the error block) but the tool handler itself
        # is never reached.
        assert any(type(e).__name__ == "ToolCallRejected" for e in events)
        assert not any(type(e).__name__ == "ToolCallApproved" for e in events)
        assert len(runner.calls) == 1
        from cairn.orchestrator._enums import ApprovalOutcome

        assert runner.calls[0]["decision"].outcome is ApprovalOutcome.REJECT

    @pytest.mark.asyncio
    async def test_unknown_tool_becomes_error_result(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                ToolCallStart(id="tc-1", name="nonexistent"),
                ToolCallEnd(id="tc-1"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="can't"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ],
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            tool_registry=DictToolRegistry(),  # empty
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        async for _ in orch.run_turn(session.id, _user("x")):
            pass
        # Two provider calls happened — the error result was fed back.
        assert len(provider.requests) == 2


# ---------------------------------------------------------------------------
# Budget, cancellation, single-turn invariant
# ---------------------------------------------------------------------------


class TestBudgetAndCancellation:
    @pytest.mark.asyncio
    async def test_budget_block_emits_and_skips_provider(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        usage_repo: UsageRepo,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
        session_repo: SessionRepo,
    ) -> None:
        from cairn.domain._enums import UsageOperation as UO

        # Pre-populate the session with spend that exceeds the cap.
        session = await session_manager.create(type=SessionType.COMPANION, persona="companion")
        await usage_repo.record(
            timestamp=frozen_clock.now(),
            session_id=session.id,
            message_id=None,
            parent_session_id=None,
            provider="x",
            model="y",
            role="primary",
            operation=UO.PRIMARY_TURN,
            cost_usd=10.00,  # > per_session cap (5.00)
        )
        cost_tracker = BasicCostTracker(
            usage_repo=usage_repo,
            clock=frozen_clock,
            budgets=BudgetConfig(per_session_usd=5.00),
        )
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )

        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)

        assert len(events) == 1
        assert isinstance(events[0], TurnBlocked)
        # Provider was never called.
        assert provider.requests == []
        # No user message persisted either (we block before any writes).
        msgs = await message_repo.list_for_session(session.id)
        assert msgs == []

    @pytest.mark.asyncio
    async def test_turn_already_running(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="x"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")

        # Start a turn but don't iterate yet.
        gen_a = orch.run_turn(session.id, _user("first"))
        await gen_a.__anext__()  # advance to first event so flag is registered

        gen_b = orch.run_turn(session.id, _user("second"))
        with pytest.raises(TurnAlreadyRunning):
            await gen_b.__anext__()

        # Drain the first generator cleanly.
        async for _ in gen_a:
            pass

    @pytest.mark.asyncio
    async def test_cancel_emits_turn_aborted(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="streaming"),
                TextDelta(text="..."),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        gen = orch.run_turn(session.id, _user("hi"))
        async for ev in gen:
            events.append(ev)
            if isinstance(ev, AssistantTextDelta):
                await orch.cancel(session.id)

        aborted = [e for e in events if isinstance(e, TurnAborted)]
        assert aborted
        assert aborted[0].reason == "user_cancel"


# ---------------------------------------------------------------------------
# Memory space threading
# ---------------------------------------------------------------------------


class TestMemoryBehaviour:
    @pytest.mark.asyncio
    async def test_retrieval_uses_session_space(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        spy = SpyMemoryService()
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            memory_service=spy,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        async for _ in orch.run_turn(session.id, _user("hi")):
            pass
        assert len(spy.calls) == 1
        assert spy.calls[0]["space"] == "companion"

    @pytest.mark.asyncio
    async def test_no_retrieval_for_ephemeral(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        spy = SpyMemoryService()
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            memory_service=spy,
        )
        session = await orch.start_session(type=SessionType.EPHEMERAL, persona="_ephemeral")
        async for _ in orch.run_turn(session.id, _user("hi")):
            pass
        # Ephemerals have no memory_space → memory service untouched.
        assert spy.calls == []

    @pytest.mark.asyncio
    async def test_no_extraction_for_ephemeral(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        extraction_spy = SpyExtractionQueue()
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
            extraction_queue=extraction_spy,
        )
        session = await orch.start_session(type=SessionType.EPHEMERAL, persona="_ephemeral")
        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)
        assert extraction_spy.submissions == []
        assert not any(isinstance(e, ObservationExtractionRequested) for e in events)


# ---------------------------------------------------------------------------
# Session lifecycle events
# ---------------------------------------------------------------------------


class TestSessionLifecycleEvents:
    @pytest.mark.asyncio
    async def test_session_created_emitted(
        self,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        assert any(
            isinstance(e, SessionCreated) and e.session_id == session.id for e in collector.events
        )


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestResumeAbortedTurns:
    @pytest.mark.asyncio
    async def test_marks_dangling_turns_aborted(
        self,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        from cairn.orchestrator._records import TurnRecord

        # Seed a session + user message + a non-terminal turn (simulating crash).
        session = await session_manager.create(type=SessionType.COMPANION, persona="companion")
        msg = Message(role="user", session_id=session.id)
        msg.content.append(TextBlock(text="hi"))
        await message_repo.append(msg)

        await turn_repo.insert(
            TurnRecord(
                id="t-crashed",
                session_id=session.id,
                user_message_id=msg.id,
                state=TurnState.PROVIDER_STREAMING,  # mid-flight
                iteration_count=0,
                model=session.model,
                started_at=frozen_clock.now(),
                completed_at=None,
                aborted_reason=None,
                stop_reason=None,
            )
        )

        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )

        resumed = await orch.resume_aborted_turns()
        assert session.id in resumed

        turn = await turn_repo.get("t-crashed")
        assert turn is not None
        assert turn.state is TurnState.ABORTED
        assert turn.aborted_reason == "process_crash"
