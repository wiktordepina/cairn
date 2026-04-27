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
    TurnIncomplete,
)
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    TextDelta,
    ToolCallDelta,
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
    preparers=(),
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
        preparers=preparers,
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
    async def test_provider_tool_call_id_collisions_across_turns(
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
        """Provider-issued tool-call ids are not always globally unique.

        Kimi via OpenRouter, in particular, emits positional names like
        ``functions.web_fetch:0`` on every turn. ``tool_calls.id`` is a
        primary key, so the second turn would fail
        ``UNIQUE constraint failed: tool_calls.id`` and abort. The
        orchestrator namespaces ids with the assistant message UUID
        before persisting; the round-trip with the provider stays opaque.
        """
        provider.scripted = [
            # Turn 1: tool call with the colliding id, then text.
            [
                ToolCallStart(id="functions.web_fetch:0", name="echo"),
                ToolCallEnd(id="functions.web_fetch:0"),
                UsageEvent(input_tokens=5, output_tokens=3),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="done-1"),
                UsageEvent(input_tokens=2, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ],
            # Turn 2: same provider id again — would have collided pre-fix.
            [
                ToolCallStart(id="functions.web_fetch:0", name="echo"),
                ToolCallEnd(id="functions.web_fetch:0"),
                UsageEvent(input_tokens=5, output_tokens=3),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="done-2"),
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

        async for _ in orch.run_turn(session.id, _user("first")):
            pass
        # The pre-fix bug surfaced here: tool_calls.id PK collision.
        async for _ in orch.run_turn(session.id, _user("second")):
            pass

        # Both turns dispatched the tool exactly once.
        assert len(runner.calls) == 2
        assert all(call["name"] == "echo" for call in runner.calls)
        # The persisted ids are distinct (namespaced by assistant
        # message id) even though the provider issued the same string
        # twice.
        ids = [call["tool_call_id"] for call in runner.calls]
        assert ids[0] != ids[1]
        assert all(tc_id.endswith(":functions.web_fetch:0") for tc_id in ids)

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
    async def test_per_turn_budget_aborts_between_iterations(
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
    ) -> None:
        """First iteration runs and records cost; the per-turn cap then
        blocks before iteration 2's provider call. Pending tool calls
        from iteration 1 are dropped on the floor."""
        # Iteration 1 issues a tool call so the loop *would* continue.
        # Iteration 2's script is never consumed because the per-turn
        # budget aborts the turn first.
        provider.scripted = [
            [
                ToolCallStart(id="tc-1", name="echo"),
                ToolCallEnd(id="tc-1"),
                # Big input_tokens so the computed cost trips the cap.
                UsageEvent(input_tokens=1_000_000, output_tokens=1_000_000),
                MessageStop(stop_reason=StopReason.TOOL_USE),
            ],
            [
                TextDelta(text="should-never-stream"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ],
        ]

        async def echo_handler(tool_call, session, turn_id):  # noqa: ARG001
            return ("echoed", False)

        runner = RecordingToolRunner(handlers={"echo": echo_handler})
        registry = DictToolRegistry(tools={"echo": StubTool(name="echo", approval_required=False)})

        # Tiny per-turn cap; daily / per-session caps stay loose so
        # only the per-turn check fires.
        cost_tracker = BasicCostTracker(
            usage_repo=usage_repo,
            clock=frozen_clock,
            budgets=BudgetConfig(
                per_turn_usd=0.0001,
                per_session_usd=1000.0,
                daily_usd=1000.0,
            ),
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
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")

        events = []
        async for ev in orch.run_turn(session.id, _user("go")):
            events.append(ev)

        # Per-turn cap aborts the turn; iteration 2 never streamed.
        aborted = [e for e in events if isinstance(e, TurnAborted)]
        assert len(aborted) == 1
        assert aborted[0].reason == "per_turn_budget"
        assert "Per-turn budget" in (aborted[0].message or "")

        # Only one provider call went out.
        assert len(provider.requests) == 1
        # Tool was never dispatched — the abort happens before tool dispatch.
        assert runner.calls == []

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
# Wall-clock turn timeout (soft-cancel via deadline watchdog)
# ---------------------------------------------------------------------------


class TestTurnTimeout:
    @pytest.mark.asyncio
    async def test_completes_under_deadline_no_cancel(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="hi"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        # Deadline well above the script's 0s wall-clock cost.
        config = OrchestratorConfig(max_iterations=5, max_turn_duration_s=10.0)
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = [ev async for ev in orch.run_turn(session.id, _user("hi"))]
        assert any(isinstance(e, TurnComplete) for e in events)
        assert not any(isinstance(e, TurnAborted) for e in events)

    @pytest.mark.asyncio
    async def test_timeout_emits_turn_aborted_with_reason(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        collector: EventCollector,
    ) -> None:
        # Stream sleeps 0.2s before yielding events; deadline is 0.05s.
        # Watchdog fires during the sleep; cancel observed at iteration
        # boundary inside the stream loop → TurnAborted(reason=turn_timeout).
        provider.pre_yield_delay_s = 0.2
        provider.scripted = [
            [
                TextDelta(text="too slow"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        config = OrchestratorConfig(max_iterations=5, max_turn_duration_s=0.05)
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = [ev async for ev in orch.run_turn(session.id, _user("hi"))]
        aborted = [e for e in events if isinstance(e, TurnAborted)]
        assert len(aborted) == 1
        assert aborted[0].reason == "turn_timeout"

    @pytest.mark.asyncio
    async def test_user_cancel_distinguished_from_timeout(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        collector: EventCollector,
    ) -> None:
        # Long deadline, explicit user cancel — must report user_cancel.
        provider.scripted = [
            [
                TextDelta(text="streaming"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        config = OrchestratorConfig(max_iterations=5, max_turn_duration_s=10.0)
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)
            if isinstance(ev, AssistantTextDelta):
                await orch.cancel(session.id)
        aborted = [e for e in events if isinstance(e, TurnAborted)]
        assert aborted
        assert aborted[0].reason == "user_cancel"

    @pytest.mark.asyncio
    async def test_watchdog_no_task_leak_under_load(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        collector: EventCollector,
    ) -> None:
        # Run 100 short turns back-to-back; assert no watchdog task
        # leaks. Each turn finishes well under the deadline so the
        # watchdog must be cancelled in the run_turn finally: block.
        import asyncio as _asyncio

        provider.scripted = [
            [
                TextDelta(text=f"hi-{i}"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
            for i in range(100)
        ]
        config = OrchestratorConfig(max_iterations=5, max_turn_duration_s=60.0)
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        for _ in range(100):
            async for _ev in orch.run_turn(session.id, _user("hi")):
                pass
        # After all turns, no watchdog tasks should be hanging around.
        leaked = [
            t for t in _asyncio.all_tasks() if t.get_name().startswith("cairn-turn-watchdog-")
        ]
        assert leaked == []


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


class TestSwapSessionModel:
    @pytest.mark.asyncio
    async def test_swaps_and_emits_event(
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
        from cairn.domain._events import ModelSwapped

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
        original_model = session.model
        new_model_id = "alternate-model"

        refreshed = await orch.swap_session_model(session.id, new_model_id, mode="keep")
        assert refreshed.model == new_model_id

        swapped = [e for e in collector.events if isinstance(e, ModelSwapped)]
        assert len(swapped) == 1
        assert swapped[0].from_model == original_model
        assert swapped[0].to_model == new_model_id
        assert swapped[0].mode == "keep"

    @pytest.mark.asyncio
    async def test_no_op_when_already_active(
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
        # Even when the requested model already matches, the
        # orchestrator emits a ModelSwapped event so observers can
        # record the user's intent — useful for "I just confirmed"
        # telemetry. The session row is not rewritten.
        from cairn.domain._events import ModelSwapped

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
        same = await orch.swap_session_model(session.id, session.model, mode="keep")
        assert same.model == session.model
        swapped = [e for e in collector.events if isinstance(e, ModelSwapped)]
        assert len(swapped) == 1
        assert swapped[0].from_model == swapped[0].to_model


# ---------------------------------------------------------------------------
# replace_collaborators (hot reload surgery)
# ---------------------------------------------------------------------------


class TestReplaceCollaborators:
    @pytest.mark.asyncio
    async def test_swap_provider_registry(
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
        del provider  # unused — we only need a baseline orchestrator.
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

        new_provider = FakeProvider(name="fake-after-reload")
        new_config = CairnConfig(
            schema_version=1,
            active_profile="test",
            providers={"fake": ProviderConfig(name="fake")},
            profiles={},
        )
        new_registry = ProviderRegistry(config=new_config, secret_resolver=None)  # type: ignore[arg-type]
        new_registry.register_adapter("fake", lambda *_: new_provider)

        orch.replace_collaborators(provider_registry=new_registry)

        assert orch._provider_registry is new_registry  # noqa: SLF001
        # Model registry is untouched when only provider_registry is swapped.
        assert orch._model_registry is model_registry  # noqa: SLF001

    def test_swap_each_collaborator_independently(
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
        del provider
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

        new_model_registry = ModelRegistry(
            [
                ModelConfig(
                    id="reloaded",
                    provider="fake",
                    display_name="Reloaded",
                    context_window=10_000,
                    max_output_tokens=512,
                    supports_tools=False,
                    input_cost_per_1m=0.5,
                    output_cost_per_1m=1.0,
                    roles={ModelRole.PRIMARY},
                ),
            ],
        )

        # Calling with no kwargs is a no-op.
        orch.replace_collaborators()
        assert orch._provider_registry is provider_registry  # noqa: SLF001
        assert orch._model_registry is model_registry  # noqa: SLF001

        # Swap only model_registry.
        orch.replace_collaborators(model_registry=new_model_registry)
        assert orch._provider_registry is provider_registry  # noqa: SLF001
        assert orch._model_registry is new_model_registry  # noqa: SLF001


# ---------------------------------------------------------------------------
# TurnIncomplete emission
# ---------------------------------------------------------------------------


class TestTurnIncompleteEmission:
    @pytest.mark.asyncio
    async def test_max_tokens_with_partial_tool_emits_turn_incomplete(
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
        # Stream a tool call that started but never finalised: ToolCallStart +
        # partial input deltas, then MessageStop(MAX_TOKENS) before any
        # ToolCallEnd. The model also emitted no text, so the iteration
        # loop exits at `if not pending_tool_calls: break`.
        provider.scripted = [
            [
                ToolCallStart(id="tc-partial", name="writer"),
                ToolCallDelta(id="tc-partial", input_delta='{"path": "fo'),
                UsageEvent(input_tokens=5, output_tokens=10),
                MessageStop(stop_reason=StopReason.MAX_TOKENS),
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
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        async for ev in orch.run_turn(session.id, _user("write a file")):
            events.append(ev)

        incomplete = [e for e in events if isinstance(e, TurnIncomplete)]
        complete = [e for e in events if isinstance(e, TurnComplete)]
        assert len(incomplete) == 1
        assert incomplete[0].session_id == session.id
        assert complete == []

    @pytest.mark.asyncio
    async def test_max_tokens_with_text_only_still_emits_turn_complete(
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
        # MAX_TOKENS with no pending tool input is a normal completion —
        # the assistant just hit the cap mid-text. We still emit
        # TurnComplete so the UI doesn't paint an "interrupted" badge.
        provider.scripted = [
            [
                TextDelta(text="A long answer that ran out of"),
                UsageEvent(input_tokens=5, output_tokens=10),
                MessageStop(stop_reason=StopReason.MAX_TOKENS),
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
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)

        assert any(isinstance(e, TurnComplete) for e in events)
        assert not any(isinstance(e, TurnIncomplete) for e in events)


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

    @pytest.mark.asyncio
    async def test_emits_structured_scan_logs(
        self,
        caplog: pytest.LogCaptureFixture,
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
        import logging as _logging

        from cairn.orchestrator._records import TurnRecord

        session = await session_manager.create(type=SessionType.COMPANION, persona="companion")
        msg = Message(role="user", session_id=session.id)
        msg.content.append(TextBlock(text="hi"))
        await message_repo.append(msg)

        await turn_repo.insert(
            TurnRecord(
                id="t-crashed-log",
                session_id=session.id,
                user_message_id=msg.id,
                state=TurnState.PROVIDER_STREAMING,
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

        with caplog.at_level(_logging.INFO, logger="cairn.orchestrator"):
            await orch.resume_aborted_turns()

        records = [r for r in caplog.records if r.name.startswith("cairn.orchestrator")]
        messages = [r.getMessage() for r in records]
        assert "resume_aborted_turns.scan_started" in messages
        assert "resume_aborted_turns.scan_complete" in messages

        per_turn = [r for r in records if r.getMessage() == "turn.resumed_as_aborted"]
        assert len(per_turn) == 1
        assert per_turn[0].turn_id == "t-crashed-log"  # type: ignore[attr-defined]
        assert per_turn[0].reason == "process_crash"  # type: ignore[attr-defined]
        assert per_turn[0].prior_state == "provider_streaming"  # type: ignore[attr-defined]

        complete = next(
            r for r in records if r.getMessage() == "resume_aborted_turns.scan_complete"
        )
        assert complete.resumed_count == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Compaction integration — overflow terminate path
# ---------------------------------------------------------------------------


class TestCompactionOverflow:
    @pytest.mark.asyncio
    async def test_overflow_terminate_aborts_turn_and_archives_session(
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
        from cairn.compaction import (
            AutoTerminateOverflowGateway,
            CompactionConfig,
            TruncatingCompactor,
        )
        from cairn.domain import SessionArchived

        # Provider will never be asked to stream — compactor will raise
        # BudgetOverflowDeclined first. Use a huge token_count so we
        # certainly exceed the advisory budget.
        provider.token_count = 1_000_000

        compactor = TruncatingCompactor(
            CompactionConfig(
                safety_margin_tokens=0,
                preserve_last_n_turns=1,
                min_history_tokens=100,
            ),
            model_registry,
            provider_registry,
            overflow_gateway=AutoTerminateOverflowGateway(),
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
            preparers=(compactor,),
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")

        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)

        aborted = [e for e in events if isinstance(e, TurnAborted)]
        archived = [e for e in events if isinstance(e, SessionArchived)]
        assert len(aborted) == 1
        assert aborted[0].reason == "user_declined_overflow"
        assert len(archived) == 1
        assert archived[0].session_id == session.id

        # Session is now archived in the DB.
        persisted = await session_manager.get(session.id)
        assert persisted.archived is True

        # Turn row reflects the abort reason.
        turns = await turn_repo.list_for_session(session.id)
        assert len(turns) == 1
        assert turns[0].state is TurnState.ABORTED
        assert turns[0].aborted_reason == "user_declined_overflow"

    @pytest.mark.asyncio
    async def test_overflow_continue_lets_request_through(
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
        from cairn.compaction import (
            AutoContinueOverflowGateway,
            CompactionConfig,
            TruncatingCompactor,
        )

        provider.token_count = 1_000_000  # always over budget
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(input_tokens=1, output_tokens=1),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]

        compactor = TruncatingCompactor(
            CompactionConfig(
                safety_margin_tokens=0,
                preserve_last_n_turns=1,
                min_history_tokens=100,
            ),
            model_registry,
            provider_registry,
            overflow_gateway=AutoContinueOverflowGateway(),
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
            preparers=(compactor,),
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")

        events = []
        async for ev in orch.run_turn(session.id, _user("hi")):
            events.append(ev)

        # Turn completed normally.
        assert any(isinstance(e, TurnComplete) for e in events)
        assert not any(isinstance(e, TurnAborted) for e in events)


# ---------------------------------------------------------------------------
# Structured logging integration — full turn lifecycle through the
# `cairn.events` logger.
# ---------------------------------------------------------------------------


class TestStructuredEventLoggingIntegration:
    @pytest.mark.asyncio
    async def test_full_turn_lifecycle_logged_in_order(
        self,
        caplog: pytest.LogCaptureFixture,
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
        import logging as _logging

        from cairn.logging import StructuredEventObserver, make_event_logger

        provider.scripted = [
            [
                TextDelta(text="hi"),
                UsageEvent(input_tokens=4, output_tokens=2),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]

        structured = StructuredEventObserver(make_event_logger("test"))
        orch = Orchestrator(
            provider_registry=provider_registry,
            model_registry=model_registry,
            session_manager=session_manager,
            context_manager=MinimalContextManager(system_prompt="you are cairn"),
            tool_registry=DictToolRegistry(),
            tool_runner=RaisingToolRunner(),
            memory_service=NullMemoryService(),
            extraction_queue=NullExtractionQueue(),
            approval_gateway=AutoApproveGateway(),
            cost_tracker=cost_tracker,
            turn_repo=turn_repo,
            message_repo=message_repo,
            clock=frozen_clock,
            config=orchestrator_config,
            observers=[collector, structured],
        )

        with caplog.at_level(_logging.INFO, logger="cairn.events"):
            session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
            async for _ in orch.run_turn(session.id, _user("hi")):
                pass

        records = [r for r in caplog.records if r.name == "cairn.events"]
        types = [r.event_type for r in records]  # type: ignore[attr-defined]

        # Session creation precedes the turn.
        assert types[0] == "SessionCreated"

        # Required lifecycle events present and in expected order.
        assert "UserMessagePersisted" in types
        assert "AssistantMessageComplete" in types
        assert "TurnComplete" in types
        assert types.index("UserMessagePersisted") < types.index("AssistantMessageComplete")
        assert types.index("AssistantMessageComplete") < types.index("TurnComplete")

        # Streaming text deltas are dropped, never logged.
        assert "AssistantTextDelta" not in types

        # Every record carries the bound profile.
        assert all(r.profile == "test" for r in records)  # type: ignore[attr-defined]

        # The TurnComplete record carries the stop reason.
        complete = next(r for r in records if r.event_type == "TurnComplete")  # type: ignore[attr-defined]
        assert complete.event["stop_reason"] == "end_turn"  # type: ignore[attr-defined]


class TestPromptCachingFlags:
    """Cache flags on the ProviderRequest are gated by
    `ModelConfig.supports_prompt_cache`."""

    @pytest.mark.asyncio
    async def test_cache_aware_when_model_supports_cache(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        # Local registry — model_cfg.supports_prompt_cache=True.
        registry = ModelRegistry(
            [
                ModelConfig(
                    id="cache-on",
                    provider="fake",
                    display_name="Cache-On",
                    context_window=100_000,
                    max_output_tokens=4_096,
                    supports_tools=True,
                    supports_prompt_cache=True,
                    input_cost_per_1m=1.0,
                    output_cost_per_1m=2.0,
                    roles={ModelRole.PRIMARY},
                )
            ]
        )
        # Re-build session_manager so it sees the new registry.
        local_sm = SessionManager(
            session_repo=session_manager._repo,  # type: ignore[attr-defined]
            model_registry=registry,
            clock=frozen_clock,
        )
        provider.scripted = [
            [
                TextDelta(text="hi"),
                UsageEvent(input_tokens=10, output_tokens=2),
                MessageStop(stop_reason=StopReason.END_TURN),
            ]
        ]
        orch = _make_orchestrator(
            provider_registry=provider_registry,
            model_registry=registry,
            session_manager=local_sm,
            message_repo=message_repo,
            turn_repo=turn_repo,
            cost_tracker=cost_tracker,
            frozen_clock=frozen_clock,
            orchestrator_config=orchestrator_config,
            collector=collector,
        )
        session = await orch.start_session(type=SessionType.COMPANION, persona="companion")
        async for _ in orch.run_turn(session.id, _user("Hi")):
            pass

        sent = provider.requests[-1]
        # cache_last_message follows from history being non-empty
        # (the user message was persisted before the request was built).
        assert sent.cache_last_message is True
        # cache_tools is False because the test session has no tools.
        assert sent.cache_tools is False

    @pytest.mark.asyncio
    async def test_cache_off_when_model_does_not_support_cache(
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
        # Default fixture model has supports_prompt_cache=False.
        provider.scripted = [
            [
                TextDelta(text="hi"),
                UsageEvent(input_tokens=10, output_tokens=2),
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
        async for _ in orch.run_turn(session.id, _user("Hi")):
            pass

        sent = provider.requests[-1]
        assert sent.cache_tools is False
        assert sent.cache_last_message is False

    @pytest.mark.asyncio
    async def test_cache_usage_persisted_to_usage_repo(
        self,
        provider: FakeProvider,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        message_repo: MessageRepo,
        turn_repo,
        cost_tracker: BasicCostTracker,
        usage_repo: UsageRepo,
        frozen_clock: FrozenClock,
        orchestrator_config: OrchestratorConfig,
        collector: EventCollector,
    ) -> None:
        provider.scripted = [
            [
                TextDelta(text="ok"),
                UsageEvent(
                    input_tokens=100,
                    output_tokens=20,
                    cache_read_tokens=80,
                    cache_write_tokens=10,
                ),
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
        async for _ in orch.run_turn(session.id, _user("Hi")):
            pass

        rows = await usage_repo.list_recent(limit=10)
        assert any(r.cache_read_tokens == 80 and r.cache_write_tokens == 10 for r in rows)
