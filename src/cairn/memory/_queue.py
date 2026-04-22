"""Observation extraction queue — orchestrator hook point.

Wraps the pure `Extractor` with:

- Non-blocking `submit()` implementing the orchestrator's
  `ExtractionQueue` protocol.
- Bounded queue with drop-oldest-on-overflow semantics.
- Per-memory-space serialisation (one in-flight extraction per space;
  spaces run in parallel).
- Length gate, persona opt-out, tool-only-turn gate.
- Graceful start/stop.

The queue owns nothing long-lived beyond the worker task + per-space
locks — all persistent state lives in the Extractor's collaborators.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cairn.domain._enums import SessionType

if TYPE_CHECKING:
    from cairn.config._models import MemoryConfig
    from cairn.memory._extractor import Extractor
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._sessions_repo import SessionRepo


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Job:
    """Internal record of one enqueued extraction request."""

    session_id: str
    since_idx: int
    turn_id: str


class ObservationExtractionQueue:
    """Post-turn extraction driver.

    Usage::

        queue = ObservationExtractionQueue(
            extractor=extractor,
            session_repo=session_repo,
            message_repo=message_repo,
            memory_config=config.active.memory,
        )
        await queue.start()
        ...
        queue.submit(session_id=..., since_idx=..., turn_id=...)
        ...
        await queue.stop()
    """

    def __init__(
        self,
        *,
        extractor: Extractor,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        memory_config: MemoryConfig,
    ) -> None:
        self._extractor = extractor
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._config = memory_config
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(
            maxsize=memory_config.max_pending_extractions,
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._space_locks: dict[str, asyncio.Lock] = {}
        self._in_flight: set[asyncio.Task[None]] = set()

    # -- Lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Start the background worker. Idempotent."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="memory-extraction-worker"
        )

    async def stop(self, *, drain_in_flight: bool = True) -> None:
        """Signal shutdown and wait for the worker to exit.

        If *drain_in_flight* is True (default), wait for currently-running
        extractions to finish before returning. Queued-but-not-started
        jobs are dropped per the design doc.
        """
        self._stop_event.set()
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except TimeoutError:
                log.warning("memory extraction worker did not stop cleanly; cancelling")
                self._worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._worker_task
            self._worker_task = None

        if drain_in_flight and self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)

    # -- Submission ----------------------------------------------------

    def submit(
        self,
        *,
        session_id: str,
        since_idx: int,
        turn_id: str,
    ) -> None:
        """Enqueue an extraction job. Never blocks.

        On queue overflow, drops the oldest queued job and logs a
        WARNING, then enqueues the new one. This matches the design-doc
        preference for fresher observations over stale ones.
        """
        job = _Job(session_id=session_id, since_idx=since_idx, turn_id=turn_id)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
                log.warning(
                    "extraction queue full (cap=%d); dropping oldest job "
                    "turn_id=%s to make room for %s",
                    self._config.max_pending_extractions,
                    dropped.turn_id,
                    turn_id,
                )
            except asyncio.QueueEmpty:  # pragma: no cover — defensive
                pass
            self._queue.put_nowait(job)

    # -- Worker loop ---------------------------------------------------

    async def _worker_loop(self) -> None:
        """Pull jobs off the queue and dispatch them per-space."""
        while not self._stop_event.is_set():
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            self._queue.task_done()

            task = asyncio.create_task(
                self._run_one(job), name=f"memory-extract-{job.turn_id}"
            )
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)

    async def _run_one(self, job: _Job) -> None:
        """Extract one job. Any exception is logged and swallowed."""
        try:
            await self._do_extract(job)
        except Exception:  # noqa: BLE001
            log.exception(
                "extraction worker: unhandled failure for turn_id=%s", job.turn_id
            )

    async def _do_extract(self, job: _Job) -> None:
        """Load transcript, apply gates, invoke the extractor.

        Per-space lock is acquired only for the extraction call itself;
        the load path is safe to run concurrently for the same space.
        """
        session = await self._session_repo.get(job.session_id)
        if session is None:
            log.warning(
                "extraction worker: session %s not found; dropping turn_id=%s",
                job.session_id,
                job.turn_id,
            )
            return

        memory_space = session.memory_space
        if memory_space is None:
            # Invariant #3: never extract from a memoryless session.
            return

        if (
            session.type == SessionType.PERSONA
            and not self._config.extract_from_personas
        ):
            return

        messages = await self._message_repo.list_for_session(job.session_id)
        latest = [m for m in messages if m.idx >= job.since_idx]
        if not latest:
            return

        # Length gate — combined text of the latest turn only.
        latest_text_len = sum(len(m.get_text()) for m in latest)
        has_latest_text = latest_text_len > 0
        if not has_latest_text and not self._config.extract_from_tool_only_turns:
            return
        if latest_text_len < self._config.min_extraction_chars:
            return

        # Context budget: rough message-count proxy for N turns.
        context_budget = max(self._config.extraction_context_turns, 0) * 4
        context_start = max(0, job.since_idx - context_budget)
        context = [
            m for m in messages if context_start <= m.idx < job.since_idx
        ]

        # Anchor the observation to the last message of the latest turn,
        # typically the assistant's final message.
        anchor_message_id = latest[-1].id

        lock = self._space_locks.setdefault(memory_space, asyncio.Lock())
        async with lock:
            await self._extractor.extract(
                memory_space=memory_space,
                turn_id=job.turn_id,
                source_session_id=job.session_id,
                source_message_id=anchor_message_id,
                context_messages=context,
                latest_messages=latest,
            )
