"""Tests for `TruncatingCompactor`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.compaction import (
    AutoContinueOverflowGateway,
    AutoTerminateOverflowGateway,
    BudgetOverflowDeclined,
    CompactionConfig,
    TruncatingCompactor,
)
from cairn.config import ModelConfig, ModelRegistry
from cairn.domain import (
    BudgetOverflowAdvisory,
    HistoryCompacted,
    Message,
    ProviderRequest,
    Session,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UIEvent,
)
from cairn.domain._enums import SessionType
from cairn.orchestrator._context import TurnContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cairn.compaction._gateway import OverflowDecision
    from cairn.domain import ProviderEvent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeProvider:
    """A provider whose `count_tokens` returns a caller-controlled value.

    The default behaviour charges a fixed cost per message so a
    shrinking message list produces a shrinking count.
    """

    per_message_tokens: int = 100
    extra_system_tokens: int = 200
    name: str = "fake"

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:  # pragma: no cover
        raise AssertionError("stream() should not be called in compactor tests")

    async def count_tokens(self, request: ProviderRequest) -> int:
        return len(request.messages) * self.per_message_tokens + self.extra_system_tokens


@dataclass
class _FakeProviderRegistry:
    provider: _FakeProvider

    def for_model(self, _model_cfg: ModelConfig) -> _FakeProvider:
        return self.provider


@dataclass
class _RecordingGateway:
    """Gateway that yields pre-configured decisions in order and records calls."""

    decisions: list[OverflowDecision] = field(default_factory=list)
    calls: list[BudgetOverflowAdvisory] = field(default_factory=list)

    async def decide(
        self,
        advisory: BudgetOverflowAdvisory,
        ctx: TurnContext,  # noqa: ARG002
    ) -> OverflowDecision:
        self.calls.append(advisory)
        if not self.decisions:
            raise AssertionError("RecordingGateway ran out of scripted decisions")
        return self.decisions.pop(0)


@dataclass
class _EventCollector:
    events: list[UIEvent] = field(default_factory=list)

    async def __call__(self, event: UIEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model(
    *,
    model_id: str = "claude-opus-4-7",
    context_window: int = 10_000,
) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        provider="fake",
        display_name="Fake",
        context_window=context_window,
        max_output_tokens=4096,
        supports_tools=True,
        input_cost_per_1m=1.0,
        output_cost_per_1m=1.0,
    )


def _registry(model_cfg: ModelConfig) -> ModelRegistry:
    return ModelRegistry([model_cfg])


def _session(session_id: str = "sess-x") -> Session:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    return Session(
        id=session_id,
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        title=None,
        archived=False,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )


def _ctx(session: Session | None = None) -> TurnContext:
    return TurnContext(
        session=session or _session(),
        turn_id="turn-x",
        iteration=0,
    )


def _user(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


def _assistant_with_tool(text: str, tool_id: str) -> Message:
    return Message(
        role="assistant",
        content=[TextBlock(text=text), ToolUseBlock(id=tool_id, name="grep", input={})],
    )


def _user_tool_result(tool_id: str) -> Message:
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content="ok")],
    )


def _plain_history(num_turns: int) -> list[Message]:
    """Return `num_turns` user/assistant message pairs."""
    out: list[Message] = []
    for i in range(num_turns):
        out.append(_user(f"q{i}"))
        out.append(_assistant(f"a{i}"))
    return out


def _request(messages: list[Message], *, max_tokens: int = 1024) -> ProviderRequest:
    return ProviderRequest(
        model="claude-opus-4-7",
        messages=messages,
        system=None,
        tools=[],
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_preparer_passes_through() -> None:
    config = CompactionConfig(enabled=False)
    preparer = TruncatingCompactor(
        config,
        _registry(_model()),
        _FakeProviderRegistry(_FakeProvider(per_message_tokens=100_000)),
    )
    request = _request(_plain_history(10))
    result = await preparer.prepare(request, _ctx())
    assert result is request


@pytest.mark.asyncio
async def test_under_budget_passes_through() -> None:
    config = CompactionConfig(safety_margin_tokens=100, preserve_last_n_turns=2)
    provider = _FakeProvider(per_message_tokens=10, extra_system_tokens=50)
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=10_000)),
        _FakeProviderRegistry(provider),
        event_sink=sink,
    )
    request = _request(_plain_history(3), max_tokens=512)
    result = await preparer.prepare(request, _ctx())
    assert result is request
    assert sink.events == []


@pytest.mark.asyncio
async def test_drops_one_block_to_fit() -> None:
    # context_window=1000, max_tokens=200, safety_margin=100 => budget=700.
    # Each message costs 100 tokens + 50 system overhead.
    config = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=1,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=1000)),
        _FakeProviderRegistry(provider),
        event_sink=sink,
    )
    # 8 messages = 800 + 50 = 850 > 700. Drop first 2-message block => 650 fits.
    request = _request(_plain_history(4), max_tokens=200)
    result = await preparer.prepare(request, _ctx())

    assert len(result.messages) == 6  # dropped one 2-message block
    compacted = [e for e in sink.events if isinstance(e, HistoryCompacted)]
    assert len(compacted) == 1
    assert compacted[0].blocks_dropped == 1
    assert compacted[0].messages_dropped == 2
    assert compacted[0].reason == "budget"
    assert compacted[0].tokens_before > compacted[0].tokens_after


@pytest.mark.asyncio
async def test_drops_multiple_blocks_to_fit() -> None:
    config = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=1,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=1000)),
        _FakeProviderRegistry(provider),
        event_sink=sink,
    )
    # 14 messages = 1450 > 700. Need to drop 4 blocks to get to 6 messages = 650.
    request = _request(_plain_history(7), max_tokens=200)
    result = await preparer.prepare(request, _ctx())

    compacted = [e for e in sink.events if isinstance(e, HistoryCompacted)]
    assert len(compacted) == 1
    assert compacted[0].blocks_dropped >= 3
    assert compacted[0].reason == "budget"
    assert len(result.messages) < len(request.messages)


@pytest.mark.asyncio
async def test_per_persona_preserve_floor_override() -> None:
    # Same history, same model — two profiles with different floors.
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    reg = _FakeProviderRegistry(provider)
    model_reg = _registry(_model(context_window=1000))

    strict = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=2,
        min_history_tokens=100,
    )
    lax = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=5,
        min_history_tokens=100,
    )

    request = _request(_plain_history(6), max_tokens=200)

    strict_prep = TruncatingCompactor(
        strict, model_reg, reg, overflow_gateway=AutoContinueOverflowGateway()
    )
    lax_prep = TruncatingCompactor(
        lax, model_reg, reg, overflow_gateway=AutoContinueOverflowGateway()
    )

    strict_result = await strict_prep.prepare(request, _ctx())
    lax_result = await lax_prep.prepare(request, _ctx())

    # Strict profile drops more because it's allowed to.
    assert len(strict_result.messages) <= len(lax_result.messages)


@pytest.mark.asyncio
async def test_preserve_floor_hit_continue_decision_lets_request_through() -> None:
    config = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=3,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    gateway = _RecordingGateway(decisions=["continue"])
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=800)),
        _FakeProviderRegistry(provider),
        overflow_gateway=gateway,
        event_sink=sink,
    )
    # budget = 800 - 200 - 100 = 500. 3-turn floor = 6 messages = 650 > 500.
    # Starting with 10 messages so there's room to try dropping before
    # hitting the floor.
    request = _request(_plain_history(5), max_tokens=200)
    result = await preparer.prepare(request, _ctx())

    assert gateway.calls  # advisory consulted
    advisories = [e for e in sink.events if isinstance(e, BudgetOverflowAdvisory)]
    compacted = [e for e in sink.events if isinstance(e, HistoryCompacted)]
    assert len(advisories) == 1
    assert len(compacted) == 1
    assert compacted[0].reason == "preserve_floor_hit"
    assert len(result.messages) == 6  # preserve_last_n_turns=3 -> 6 messages


@pytest.mark.asyncio
async def test_preserve_floor_hit_terminate_decision_raises() -> None:
    config = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=3,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    gateway = _RecordingGateway(decisions=["terminate"])
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=800)),
        _FakeProviderRegistry(provider),
        overflow_gateway=gateway,
        event_sink=sink,
    )
    request = _request(_plain_history(5), max_tokens=200)

    with pytest.raises(BudgetOverflowDeclined) as info:
        await preparer.prepare(request, _ctx())

    assert info.value.session_id == "sess-x"
    assert info.value.turn_id == "turn-x"
    # Advisory fired, but no HistoryCompacted event emitted (we aborted).
    assert any(isinstance(e, BudgetOverflowAdvisory) for e in sink.events)
    assert not any(isinstance(e, HistoryCompacted) for e in sink.events)


@pytest.mark.asyncio
async def test_default_gateway_is_auto_terminate() -> None:
    """Sanity: constructing without an explicit gateway uses the
    fail-loud default."""
    config = CompactionConfig(
        safety_margin_tokens=100,
        preserve_last_n_turns=3,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=50)
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=800)),
        _FakeProviderRegistry(provider),
    )
    request = _request(_plain_history(5), max_tokens=200)
    with pytest.raises(BudgetOverflowDeclined):
        await preparer.prepare(request, _ctx())


@pytest.mark.asyncio
async def test_min_history_floor_passes_through_with_error_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # context_window tiny so budget is below min_history_tokens.
    config = CompactionConfig(
        safety_margin_tokens=100,
        min_history_tokens=5000,
    )
    provider = _FakeProvider(per_message_tokens=1000, extra_system_tokens=0)
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=2000)),
        _FakeProviderRegistry(provider),
        event_sink=sink,
    )
    request = _request(_plain_history(3), max_tokens=1024)

    with caplog.at_level("ERROR", logger="cairn.compaction._preparer"):
        result = await preparer.prepare(request, _ctx())

    assert result is request
    assert any("below min_history_tokens" in r.message for r in caplog.records)
    assert sink.events == []


@pytest.mark.asyncio
async def test_tool_pair_integrity_preserved() -> None:
    # Force dropping a turn with a tool pair — the surviving messages
    # must still have matched tool_use_id <-> tool_use_id entries.
    config = CompactionConfig(
        safety_margin_tokens=0,
        preserve_last_n_turns=1,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=0)
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=800)),
        _FakeProviderRegistry(provider),
    )
    messages = [
        _user("find something"),
        _assistant_with_tool("searching", "t1"),
        _user_tool_result("t1"),
        _assistant("done"),
        _user("now something else"),
        _assistant_with_tool("searching", "t2"),
        _user_tool_result("t2"),
        _assistant("done too"),
    ]
    request = _request(messages, max_tokens=200)
    result = await preparer.prepare(request, _ctx())

    # Collect surviving tool_use ids and tool_result ids.
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for msg in result.messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_use_ids.add(block.id)
            elif isinstance(block, ToolResultBlock):
                tool_result_ids.add(block.tool_use_id)
    assert tool_use_ids == tool_result_ids


@pytest.mark.asyncio
async def test_first_message_after_compaction_is_real_user_turn() -> None:
    config = CompactionConfig(
        safety_margin_tokens=0,
        preserve_last_n_turns=1,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=0)
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=400)),
        _FakeProviderRegistry(provider),
    )
    request = _request(_plain_history(5), max_tokens=100)
    result = await preparer.prepare(request, _ctx())

    assert result.messages[0].role == "user"
    first = result.messages[0]
    # Not purely tool results.
    assert any(not isinstance(b, ToolResultBlock) for b in first.content)


@pytest.mark.asyncio
async def test_fits_without_dropping_when_exactly_at_budget() -> None:
    # Edge: tokens_before == effective_budget exactly.
    config = CompactionConfig(
        safety_margin_tokens=0,
        preserve_last_n_turns=2,
        min_history_tokens=100,
    )
    provider = _FakeProvider(per_message_tokens=100, extra_system_tokens=0)
    sink = _EventCollector()
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=700)),
        _FakeProviderRegistry(provider),
        event_sink=sink,
    )
    # budget = 700 - 200 = 500. 5 messages = 500 -> fits exactly.
    messages = [_user("a"), _assistant("A"), _user("b"), _assistant("B"), _user("c")]
    request = _request(messages, max_tokens=200)
    result = await preparer.prepare(request, _ctx())

    assert result is request
    assert sink.events == []


@pytest.mark.asyncio
async def test_terminate_gateway_stub_raises_declined() -> None:
    config = CompactionConfig(
        safety_margin_tokens=0,
        preserve_last_n_turns=2,
        min_history_tokens=100,
    )
    # 2-turn preserve floor = 4 messages * 150 = 600 > budget 200.
    provider = _FakeProvider(per_message_tokens=150, extra_system_tokens=0)
    preparer = TruncatingCompactor(
        config,
        _registry(_model(context_window=300)),
        _FakeProviderRegistry(provider),
        overflow_gateway=AutoTerminateOverflowGateway(),
    )
    request = _request(_plain_history(5), max_tokens=100)
    with pytest.raises(BudgetOverflowDeclined):
        await preparer.prepare(request, _ctx())
