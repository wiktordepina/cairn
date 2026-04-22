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

    async def extract(self, **kwargs: Any) -> ExtractionResult:
        self.calls.append(kwargs)
        if self.start_events:
            ev = self.start_events.pop(0)
            ev.set()
        if self.release_event is not None:
            await self.release_event.wait()
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        return ExtractionResult(observations_written=1)


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
) -> ObservationExtractionQueue:
    return ObservationExtractionQueue(
        extractor=extractor,  # type: ignore[arg-type]
        session_repo=session_repo,
        message_repo=message_repo,
        memory_config=memory_config or MemoryConfig(min_extraction_chars=20),
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
