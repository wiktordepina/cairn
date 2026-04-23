"""Tests for ObservationExtractionQueue — the gating + dispatch layer.

Uses a fake Extractor so tests run without the full streaming path;
that path is exercised in test_extractor.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from cairn.config._models import MemoryConfig
from cairn.domain._content import TextBlock
from cairn.domain._enums import SessionType
from cairn.domain._events import ObservationExtractionCompleted, UIEvent
from cairn.domain._messages import Message
from cairn.domain._sessions import Session
from cairn.memory._extractor import ExtractionResult
from cairn.memory._queue import ObservationExtractionQueue
from cairn.persistence._messages_repo import MessageRepo
from cairn.persistence._sessions_repo import SessionRepo

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeExtractor:
    """Records calls to extract() and returns a canned result."""

    calls: list[dict[str, Any]] = field(default_factory=list)  # type: ignore[assignment]
    delay_s: float = 0.0
    start_events: list[asyncio.Event] = field(default_factory=list)  # type: ignore[assignment]
    release_event: asyncio.Event | None = None

    result: ExtractionResult | None = None
    raise_exc: BaseException | None = None

    async def extract(self, **kwargs: Any) -> ExtractionResult:
        self.calls.append(kwargs)
        if self.start_events:
            ev = self.start_events.pop(0)
            ev.set()
        if self.release_event is not None:
            await self.release_event.wait()
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result if self.result is not None else ExtractionResult(observations_written=1)


@dataclass
class _RecordingObserver:
    """Captures fanned-out events for assertion. Matches UIEventObserver."""

    events: list[UIEvent] = field(default_factory=list)  # type: ignore[assignment]

    def observe(self, event: UIEvent) -> None:
        self.events.append(event)


@dataclass
class _RaisingObserver:
    """Always raises. Used to confirm fan-out is fault-tolerant."""

    def observe(self, event: UIEvent) -> None:
        raise RuntimeError("observer boom")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_session(
    db,
    *,
    session_id: str = "sess-1",
    session_type: SessionType = SessionType.COMPANION,
    memory_space: str | None = "companion",
) -> None:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    session = Session(
        id=session_id,
        type=session_type,
        persona="companion",
        model="claude-opus-4-7",
        memory_space=memory_space,
        created_at=now,
        updated_at=now,
    )
    await SessionRepo(db).insert(session)


async def _seed_turn(
    db,
    *,
    session_id: str,
    turn_id: str,
    user_text: str,
    assistant_text: str,
) -> int:
    """Write a user + assistant pair tagged with *turn_id*; return
    since_idx (the idx of the first message in this turn)."""
    repo = MessageRepo(db)
    user = Message(role="user", session_id=session_id)
    user.content.append(TextBlock(text=user_text))
    await repo.append(user, turn_id=turn_id)
    since_idx = user.idx
    assistant = Message(role="assistant", session_id=session_id)
    assistant.content.append(TextBlock(text=assistant_text))
    await repo.append(assistant, turn_id=turn_id)
    return since_idx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_repo(db) -> SessionRepo:
    return SessionRepo(db)


@pytest_asyncio.fixture
async def message_repo(db) -> MessageRepo:
    return MessageRepo(db)


def _make_queue(
    *,
    extractor,
    session_repo,
    message_repo,
    memory_config: MemoryConfig | None = None,
    observers: tuple[Any, ...] = (),
) -> ObservationExtractionQueue:
    return ObservationExtractionQueue(
        extractor=extractor,  # type: ignore[arg-type]
        session_repo=session_repo,
        message_repo=message_repo,
        memory_config=memory_config or MemoryConfig(min_extraction_chars=20),
        observers=observers,
    )


async def _drain(queue: ObservationExtractionQueue, *, timeout: float = 1.0) -> None:
    """Wait for the queue to drain and any in-flight tasks to complete."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if queue._queue.empty() and not queue._in_flight:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("queue did not drain within timeout")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_submit_triggers_extraction(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-1",
            user_text="Do we have time for a quick architectural diversion?",
            assistant_text="Yes, I'd like to discuss the trade-offs here",
        )

        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert len(extractor.calls) == 1
        call = extractor.calls[0]
        assert call["memory_space"] == "companion"
        assert call["turn_id"] == "turn-1"
        assert len(call["latest_messages"]) == 2


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestGates:
    @pytest.mark.asyncio
    async def test_length_gate_skips_short_turn(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-1",
            user_text="thanks",
            assistant_text="ok",
        )

        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=MemoryConfig(min_extraction_chars=200),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert extractor.calls == []

    @pytest.mark.asyncio
    async def test_memoryless_session_skipped(self, db, session_repo, message_repo):
        await _seed_session(db, memory_space=None)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-1",
            user_text="some user text with enough content here",
            assistant_text="some assistant text with enough content here too",
        )

        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert extractor.calls == []

    @pytest.mark.asyncio
    async def test_persona_opt_out(self, db, session_repo, message_repo):
        await _seed_session(db, session_type=SessionType.PERSONA, memory_space="persona:alex")
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-1",
            user_text="persona user text that is plenty long enough",
            assistant_text="persona assistant reply that is equally long",
        )

        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=MemoryConfig(extract_from_personas=False, min_extraction_chars=20),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert extractor.calls == []

    @pytest.mark.asyncio
    async def test_persona_extraction_enabled_by_default(self, db, session_repo, message_repo):
        await _seed_session(db, session_type=SessionType.PERSONA, memory_space="persona:alex")
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-1",
            user_text="persona user text that is plenty long enough",
            assistant_text="persona assistant reply that is equally long",
        )

        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert len(extractor.calls) == 1
        assert extractor.calls[0]["memory_space"] == "persona:alex"

    @pytest.mark.asyncio
    async def test_missing_session_logged_and_skipped(
        self, db, session_repo, message_repo, caplog
    ):
        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        queue.submit(session_id="ghost", since_idx=0, turn_id="turn-1")
        await _drain(queue)
        await queue.stop()

        assert extractor.calls == []


# ---------------------------------------------------------------------------
# Queue overflow
# ---------------------------------------------------------------------------


class TestOverflow:
    @pytest.mark.asyncio
    async def test_overflow_drops_oldest_and_warns(self, db, session_repo, message_repo, caplog):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-any",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        # Build the queue before starting its worker so we can stuff it
        # without anything being drained.
        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=MemoryConfig(min_extraction_chars=20, max_pending_extractions=2),
        )

        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="first")
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="second")

        import logging

        with caplog.at_level(logging.WARNING, logger="cairn.memory._queue"):
            queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="third")

        assert any("dropping oldest" in r.message for r in caplog.records)

        await queue.start()
        await _drain(queue)
        await queue.stop()

        seen = {c["turn_id"] for c in extractor.calls}
        # "first" was dropped; "second" + "third" survived.
        assert "first" not in seen
        assert {"second", "third"}.issubset(seen)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_per_space_serialisation(self, db, session_repo, message_repo):
        """Two jobs for the same space must not run in parallel."""
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="turn-any",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        release = asyncio.Event()
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        extractor = _FakeExtractor(
            release_event=release,
            start_events=[first_started, second_started],
        )
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()

        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-1")
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-2")

        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        # The second must NOT have started yet — the per-space lock blocks it.
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        release.set()
        await _drain(queue)
        await queue.stop()

        assert len(extractor.calls) == 2

    @pytest.mark.asyncio
    async def test_cross_space_parallelism(self, db, session_repo, message_repo):
        """Jobs on different spaces are free to run in parallel."""
        await _seed_session(db, session_id="sess-a", memory_space="space-a")
        since_a = await _seed_turn(
            db,
            session_id="sess-a",
            turn_id="t-a",
            user_text="user text that is plenty long enough A",
            assistant_text="assistant text that is plenty long enough A",
        )
        await _seed_session(db, session_id="sess-b", memory_space="space-b")
        since_b = await _seed_turn(
            db,
            session_id="sess-b",
            turn_id="t-b",
            user_text="user text that is plenty long enough B",
            assistant_text="assistant text that is plenty long enough B",
        )

        release = asyncio.Event()
        a_started = asyncio.Event()
        b_started = asyncio.Event()
        extractor = _FakeExtractor(
            release_event=release,
            start_events=[a_started, b_started],
        )
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()

        queue.submit(session_id="sess-a", since_idx=since_a, turn_id="t-a")
        queue.submit(session_id="sess-b", since_idx=since_b, turn_id="t-b")

        # Both jobs should enter extract() before either completes.
        await asyncio.wait_for(a_started.wait(), timeout=1.0)
        await asyncio.wait_for(b_started.wait(), timeout=1.0)

        release.set()
        await _drain(queue)
        await queue.stop()

        assert len(extractor.calls) == 2


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, db, session_repo, message_repo):
        extractor = _FakeExtractor()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        task1 = queue._worker_task
        await queue.start()
        assert queue._worker_task is task1
        await queue.stop()

    @pytest.mark.asyncio
    async def test_stop_drains_in_flight(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-1",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        release = asyncio.Event()
        first_started = asyncio.Event()
        extractor = _FakeExtractor(
            release_event=release,
            start_events=[first_started],
        )
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()

        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-1")
        await asyncio.wait_for(first_started.wait(), timeout=1.0)

        # Kick off stop(); it must wait for the in-flight extraction.
        stop_task = asyncio.create_task(queue.stop())
        await asyncio.sleep(0.05)
        assert not stop_task.done()

        release.set()
        await asyncio.wait_for(stop_task, timeout=1.0)
        assert len(extractor.calls) == 1


# ---------------------------------------------------------------------------
# ObservationExtractionCompleted fan-out
# ---------------------------------------------------------------------------


class TestCompletionEvents:
    """Every post-submit terminal path fans out exactly one
    `ObservationExtractionCompleted` that pairs with the orchestrator's
    earlier `ObservationExtractionRequested`.
    """

    @staticmethod
    def _only_completion(observer: _RecordingObserver) -> ObservationExtractionCompleted:
        completions = [e for e in observer.events if isinstance(e, ObservationExtractionCompleted)]
        assert len(completions) == 1, f"expected 1 completion event, got {observer.events}"
        return completions[0]

    @pytest.mark.asyncio
    async def test_success_fans_out_succeeded(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-1",
            user_text="a substantive user message worth extracting from",
            assistant_text="a substantive assistant reply that mentions something memorable",
        )

        extractor = _FakeExtractor(
            result=ExtractionResult(observations_written=3, cost_usd=0.0012),
        )
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-1")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.session_id == "sess-1"
        assert ev.turn_id == "t-1"
        assert ev.status == "succeeded"
        assert ev.observations_written == 3
        assert ev.cost_usd == pytest.approx(0.0012)
        assert ev.reason is None

    @pytest.mark.asyncio
    async def test_cost_cap_with_partial_observations_still_succeeded(
        self, db, session_repo, message_repo
    ):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-2",
            user_text="enough text to pass the length gate no bother",
            assistant_text="and a reply that's plenty long enough too",
        )

        extractor = _FakeExtractor(
            result=ExtractionResult(
                observations_written=1,
                cost_usd=0.05,
                truncated_by_cost_cap=True,
            ),
        )
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-2")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "succeeded"
        assert ev.reason == "cost_cap_partial"
        assert ev.observations_written == 1

    @pytest.mark.asyncio
    async def test_cost_cap_with_no_observations_is_failed(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-3",
            user_text="enough text to pass the length gate no bother",
            assistant_text="and a reply that's plenty long enough too",
        )

        extractor = _FakeExtractor(
            result=ExtractionResult(
                observations_written=0,
                cost_usd=0.05,
                truncated_by_cost_cap=True,
            ),
        )
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-3")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "failed"
        assert ev.reason == "cost_cap"

    @pytest.mark.asyncio
    async def test_parse_failure_is_failed(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-4",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        extractor = _FakeExtractor(result=ExtractionResult(parse_failed=True))
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-4")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "failed"
        assert ev.reason == "parse_failed"

    @pytest.mark.asyncio
    async def test_extractor_exception_is_failed(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-5",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        extractor = _FakeExtractor(raise_exc=RuntimeError("provider went pop"))
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-5")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "failed"
        assert ev.reason == "exception"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("setup_kwargs", "memory_config_kwargs", "expected_reason"),
        [
            pytest.param(
                {"memory_space": None},
                {},
                "memoryless",
                id="memoryless_session",
            ),
            pytest.param(
                {"session_type": SessionType.PERSONA},
                {"extract_from_personas": False},
                "persona_opt_out",
                id="persona_opt_out",
            ),
        ],
    )
    async def test_gates_fan_out_with_reason(
        self,
        db,
        session_repo,
        message_repo,
        setup_kwargs,
        memory_config_kwargs,
        expected_reason,
    ):
        await _seed_session(db, **setup_kwargs)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-gate",
            user_text="user text that is plenty long enough to satisfy length",
            assistant_text="and a reply that's plenty long enough too for the gate",
        )

        extractor = _FakeExtractor()
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=MemoryConfig(min_extraction_chars=20, **memory_config_kwargs),
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-gate")
        await _drain(queue)
        await queue.stop()

        assert extractor.calls == []
        ev = self._only_completion(observer)
        assert ev.status == "gated"
        assert ev.reason == expected_reason

    @pytest.mark.asyncio
    async def test_length_gate_fans_out_with_reason(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-short",
            user_text="thanks",
            assistant_text="ok",
        )

        extractor = _FakeExtractor()
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=MemoryConfig(min_extraction_chars=200),
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-short")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "gated"
        assert ev.reason == "too_short"

    @pytest.mark.asyncio
    async def test_missing_session_fans_out_gated(self, db, session_repo, message_repo):
        extractor = _FakeExtractor()
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        # No session seeded — `_do_extract` short-circuits with a log warning.
        queue.submit(session_id="no-such-session", since_idx=0, turn_id="t-missing")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "gated"
        assert ev.reason == "session_missing"

    @pytest.mark.asyncio
    async def test_no_latest_messages_fans_out_gated(self, db, session_repo, message_repo):
        await _seed_session(db)
        # Seed a turn but submit with since_idx past the last message.
        await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-old",
            user_text="a substantive user message worth extracting from",
            assistant_text="a substantive assistant reply worth extracting from",
        )

        extractor = _FakeExtractor()
        observer = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(observer,),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=999, turn_id="t-empty")
        await _drain(queue)
        await queue.stop()

        ev = self._only_completion(observer)
        assert ev.status == "gated"
        assert ev.reason == "no_latest_messages"

    @pytest.mark.asyncio
    async def test_no_observers_does_not_crash(self, db, session_repo, message_repo):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-silent",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        extractor = _FakeExtractor()
        # No observers kwarg — default is ().
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-silent")
        await _drain(queue)
        await queue.stop()

        assert len(extractor.calls) == 1

    @pytest.mark.asyncio
    async def test_raising_observer_does_not_break_others(
        self, db, session_repo, message_repo, caplog
    ):
        await _seed_session(db)
        since_idx = await _seed_turn(
            db,
            session_id="sess-1",
            turn_id="t-boom",
            user_text="user text that is plenty long enough",
            assistant_text="assistant text that is plenty long enough",
        )

        extractor = _FakeExtractor()
        raising = _RaisingObserver()
        recording = _RecordingObserver()
        queue = _make_queue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            observers=(raising, recording),
        )
        await queue.start()
        queue.submit(session_id="sess-1", since_idx=since_idx, turn_id="t-boom")
        await _drain(queue)
        await queue.stop()

        # The recording observer still received the event even though
        # `raising` blew up first.
        assert len(recording.events) == 1
        assert isinstance(recording.events[0], ObservationExtractionCompleted)
        assert "extraction queue observer failed" in caplog.text.lower()
