"""Tests for the Extractor engine — the pure extraction path without
queue / gating logic.

Uses a FakeProvider that replays scripted events so we can drive the
full streaming path deterministically. Avoids the real LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from cairn.config._models import ModelConfig, ModelRole
from cairn.config._registry import ModelRegistry
from cairn.domain._enums import (
    MemoryClass,
    MemoryEntryType,
    StopReason,
    UsageOperation,
)
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    TextDelta,
    UsageEvent,
)
from cairn.memory._extractor import (
    Extractor,
    _memory_class_for,
    _parse_response,
    render_transcript,
)
from cairn.memory._observation_log import ObservationLog
from cairn.orchestrator._clock import FrozenClock
from cairn.persistence._memory_repo import MemoryRepo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cairn.domain._provider import ProviderEvent, ProviderRequest


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    """Replays a single scripted event list per call to `stream()`."""

    name: str = "fake"
    scripted: list[list[ProviderEvent]] = field(default_factory=list)  # type: ignore[assignment]
    requests: list[ProviderRequest] = field(default_factory=list)  # type: ignore[assignment]
    _cursor: int = 0

    def stream(self, request: ProviderRequest):
        self.requests.append(request)
        if self._cursor >= len(self.scripted):
            raise AssertionError("FakeProvider exhausted")
        events = self.scripted[self._cursor]
        self._cursor += 1
        return _async_iter(events)

    async def count_tokens(self, request: ProviderRequest) -> int:  # pragma: no cover
        return 0


async def _async_iter(events: list[ProviderEvent]) -> AsyncIterator[ProviderEvent]:
    for ev in events:
        yield ev


@dataclass
class _CannedProviderRegistry:
    """Returns the same fake provider regardless of model."""

    provider: FakeProvider

    def for_model(self, model_config: ModelConfig) -> FakeProvider:  # noqa: ARG002
        return self.provider


@dataclass
class _RecordingCostTracker:
    """Records every `record()` call without hitting a real UsageRepo."""

    records: list[dict[str, Any]] = field(default_factory=list)  # type: ignore[assignment]

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)

    async def should_block_turn(self, *, session_id: str):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


EXTRACTION_MODEL = ModelConfig(
    id="extraction-model",
    provider="fake",
    display_name="Extraction Model",
    context_window=8000,
    max_output_tokens=1024,
    supports_tools=False,
    input_cost_per_1m=1.0,
    output_cost_per_1m=5.0,
    roles={ModelRole.EXTRACTION},
)

UTILITY_MODEL = ModelConfig(
    id="utility-model",
    provider="fake",
    display_name="Utility Model",
    context_window=8000,
    max_output_tokens=1024,
    supports_tools=False,
    input_cost_per_1m=0.5,
    output_cost_per_1m=2.5,
    roles={ModelRole.UTILITY},
)


def _memory_config(**overrides: Any):
    from cairn.config._models import MemoryConfig

    return MemoryConfig(**overrides)


@pytest_asyncio.fixture
async def memory_repo(db):
    return MemoryRepo(db)


@pytest.fixture
def observation_log(tmp_path):
    return ObservationLog(tmp_path / "memory")


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 4, 22, 12, 0, tzinfo=UTC))


def _build_extractor(
    *,
    provider: FakeProvider,
    memory_repo: MemoryRepo,
    observation_log: ObservationLog,
    clock: FrozenClock,
    models: list[ModelConfig] | None = None,
    memory_config: Any = None,
) -> tuple[Extractor, _RecordingCostTracker]:
    registry = ModelRegistry(models or [EXTRACTION_MODEL, UTILITY_MODEL])
    cost_tracker = _RecordingCostTracker()
    extractor = Extractor(
        memory_repo=memory_repo,
        observation_log=observation_log,
        provider_registry=_CannedProviderRegistry(provider=provider),  # type: ignore[arg-type]
        model_registry=registry,
        cost_tracker=cost_tracker,  # type: ignore[arg-type]
        clock=clock,
        memory_config=memory_config or _memory_config(),
    )
    return extractor, cost_tracker


def _text_msg(text: str, role: str = "user") -> Message:
    msg = Message(role=role, session_id="s")  # type: ignore[arg-type]
    msg.append_text_delta(text)
    return msg


def _scripted_json(
    response: str,
    *,
    usage: UsageEvent | None = None,
    stop: StopReason = StopReason.END_TURN,
) -> list[ProviderEvent]:
    events: list[ProviderEvent] = [TextDelta(text=response)]
    events.append(usage or UsageEvent(input_tokens=100, output_tokens=20))
    events.append(MessageStop(stop_reason=stop))
    return events


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_memory_class_for_event_is_episodic(self) -> None:
        assert _memory_class_for(MemoryEntryType.EVENT) == MemoryClass.EPISODIC

    def test_memory_class_for_task_is_episodic(self) -> None:
        assert _memory_class_for(MemoryEntryType.TASK) == MemoryClass.EPISODIC

    def test_memory_class_for_fact_is_semantic(self) -> None:
        assert _memory_class_for(MemoryEntryType.FACT) == MemoryClass.SEMANTIC

    def test_render_transcript_labels_context_and_latest(self) -> None:
        ctx = [_text_msg("earlier thing", role="user")]
        latest = [
            _text_msg("now user says", role="user"),
            _text_msg("assistant replies", role="assistant"),
        ]
        out = render_transcript(context_messages=ctx, latest_messages=latest)
        assert "[context] User: earlier thing" in out
        assert "[LATEST] User: now user says" in out
        assert "[LATEST] Assistant: assistant replies" in out


class TestResponseParsing:
    def test_plain_json(self) -> None:
        raw = '{"observations": [{"content": "x", "entry_type": "fact", "importance": 5}]}'
        parsed = _parse_response(raw)
        assert parsed is not None
        assert len(parsed.observations) == 1
        assert parsed.observations[0].content == "x"

    def test_fenced_json(self) -> None:
        raw = '```json\n{"observations": []}\n```'
        parsed = _parse_response(raw)
        assert parsed is not None
        assert parsed.observations == []

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_response("not json at all") is None

    def test_wrong_schema_returns_none(self) -> None:
        raw = '{"observations": [{"content": "x", "entry_type": "bogus", "importance": 5}]}'
        assert _parse_response(raw) is None

    def test_importance_out_of_range_rejected(self) -> None:
        raw = '{"observations": [{"content": "x", "entry_type": "fact", "importance": 99}]}'
        assert _parse_response(raw) is None


# ---------------------------------------------------------------------------
# Extractor.resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_uses_extraction_role_when_present(
        self, memory_repo, observation_log, clock
    ):
        provider = FakeProvider()
        extractor, _ = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
        )
        model = extractor.resolve_model()
        assert model.id == "extraction-model"

    def test_falls_back_to_utility(self, memory_repo, observation_log, clock):
        provider = FakeProvider()
        extractor, _ = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
            models=[UTILITY_MODEL],  # no EXTRACTION role
        )
        model = extractor.resolve_model()
        assert model.id == "utility-model"


# ---------------------------------------------------------------------------
# Full extraction flow
# ---------------------------------------------------------------------------


class TestExtractFlow:
    @pytest.mark.asyncio
    async def test_happy_path_writes_to_repo_log_and_usage(
        self, memory_repo, observation_log, clock, seed_session
    ):
        provider = FakeProvider(
            scripted=[
                _scripted_json(
                    '{"observations": ['
                    '{"content": "User prefers British English", '
                    '"entry_type": "preference", "importance": 7}'
                    "]}"
                )
            ]
        )
        extractor, cost_tracker = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
        )
        result = await extractor.extract(
            memory_space="companion",
            turn_id="turn-1",
            source_session_id="sess-1",
            source_message_id="msg-1",
            context_messages=[],
            latest_messages=[_text_msg("I prefer British English spelling")],
        )

        assert result.observations_written == 1
        stored = await memory_repo.recent("companion")
        assert len(stored) == 1
        assert stored[0].entry_type == MemoryEntryType.PREFERENCE
        assert stored[0].importance == 7
        assert stored[0].memory_class == MemoryClass.SEMANTIC

        jsonl_path = observation_log.path_for(clock.now())
        assert jsonl_path.exists()
        assert jsonl_path.read_text().count("\n") == 1

        assert len(cost_tracker.records) == 1
        rec = cost_tracker.records[0]
        assert rec["operation"] == UsageOperation.EXTRACTION
        assert rec["role"] == ModelRole.EXTRACTION.value

    @pytest.mark.asyncio
    async def test_invalid_json_drops_batch_no_write(
        self, memory_repo, observation_log, clock, seed_session
    ):
        provider = FakeProvider(scripted=[_scripted_json("not valid json")])
        extractor, cost_tracker = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
        )
        result = await extractor.extract(
            memory_space="companion",
            turn_id="turn-1",
            source_session_id="sess-1",
            source_message_id="msg-1",
            context_messages=[],
            latest_messages=[_text_msg("hello")],
        )

        assert result.parse_failed
        assert result.observations_written == 0
        assert await memory_repo.count_for_space("companion") == 0
        # Usage still recorded — we paid for the call.
        assert len(cost_tracker.records) == 1

    @pytest.mark.asyncio
    async def test_cost_cap_truncates_mid_stream(
        self, memory_repo, observation_log, clock, seed_session
    ):
        # Usage event triggers cost cap before any text arrives.
        scripted: list[ProviderEvent] = [
            UsageEvent(input_tokens=1_000_000, output_tokens=1_000_000),
            TextDelta(text='{"observations": []}'),
            MessageStop(stop_reason=StopReason.END_TURN),
        ]
        provider = FakeProvider(scripted=[scripted])
        extractor, _ = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
            memory_config=_memory_config(max_extraction_cost_usd=0.001),
        )
        result = await extractor.extract(
            memory_space="companion",
            turn_id="turn-1",
            source_session_id="sess-1",
            source_message_id="msg-1",
            context_messages=[],
            latest_messages=[_text_msg("some long-enough text")],
        )
        assert result.truncated_by_cost_cap

    @pytest.mark.asyncio
    async def test_provider_stream_error_does_not_raise(
        self, memory_repo, observation_log, clock, seed_session
    ):
        class _BadProvider(FakeProvider):
            def stream(self, request):  # type: ignore[override]
                raise RuntimeError("network exploded")

        provider = _BadProvider()
        extractor, _ = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
        )
        result = await extractor.extract(
            memory_space="companion",
            turn_id="turn-1",
            source_session_id="sess-1",
            source_message_id="msg-1",
            context_messages=[],
            latest_messages=[_text_msg("hello")],
        )
        assert result.parse_failed

    @pytest.mark.asyncio
    async def test_multiple_observations_all_stored(
        self, memory_repo, observation_log, clock, seed_session
    ):
        payload = (
            '{"observations": ['
            '{"content": "Fact one goes here",'
            ' "entry_type": "fact", "importance": 5},'
            '{"content": "A preference worth keeping",'
            ' "entry_type": "preference", "importance": 6},'
            '{"content": "Task scheduled for tomorrow",'
            ' "entry_type": "task", "importance": 4}'
            "]}"
        )
        provider = FakeProvider(scripted=[_scripted_json(payload)])
        extractor, _ = _build_extractor(
            provider=provider,
            memory_repo=memory_repo,
            observation_log=observation_log,
            clock=clock,
        )
        result = await extractor.extract(
            memory_space="companion",
            turn_id="turn-1",
            source_session_id="sess-1",
            source_message_id="msg-1",
            context_messages=[],
            latest_messages=[_text_msg("a turn that yields three observations")],
        )

        assert result.observations_written == 3
        stored = await memory_repo.recent("companion")
        assert len(stored) == 3
        # Task should be episodic, others semantic.
        by_type = {e.entry_type: e for e in stored}
        assert by_type[MemoryEntryType.TASK].memory_class == MemoryClass.EPISODIC
        assert by_type[MemoryEntryType.FACT].memory_class == MemoryClass.SEMANTIC
