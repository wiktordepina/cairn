"""Tests for TurnRepo."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import StopReason
from cairn.orchestrator._enums import TurnState
from cairn.orchestrator._records import TurnRecord
from cairn.persistence._turns_repo import InvalidTurnTransition, TurnRepo

from .conftest import make_db_message, make_db_session

if TYPE_CHECKING:
    from cairn.persistence._connection import Database
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._sessions_repo import SessionRepo


@pytest.fixture
def turn_repo(db: Database) -> TurnRepo:
    return TurnRepo(db)


@pytest.fixture
def started_at() -> datetime:
    return datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


async def _seed(
    *,
    session_repo: SessionRepo,
    message_repo: MessageRepo,
    session_id: str = "sess-1",
    message_id: str = "msg-1",
) -> None:
    await session_repo.insert(make_db_session(id=session_id))
    msg = make_db_message(id=message_id, session_id=session_id)
    await message_repo.append(msg)


def _record(
    *,
    id: str = "t-1",
    state: TurnState = TurnState.STARTED,
    session_id: str = "sess-1",
    message_id: str = "msg-1",
    started_at: datetime,
) -> TurnRecord:
    return TurnRecord(
        id=id,
        session_id=session_id,
        user_message_id=message_id,
        state=state,
        iteration_count=0,
        model="claude-opus-4-7",
        started_at=started_at,
        completed_at=None,
        aborted_reason=None,
        stop_reason=None,
    )


class TestInsertAndGet:
    @pytest.mark.asyncio
    async def test_round_trip(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        rec = _record(started_at=started_at)
        await turn_repo.insert(rec)
        got = await turn_repo.get("t-1")
        assert got is not None
        assert got.state is TurnState.STARTED
        assert got.session_id == "sess-1"
        assert got.user_message_id == "msg-1"
        assert got.iteration_count == 0

    @pytest.mark.asyncio
    async def test_missing_returns_none(self, turn_repo: TurnRepo) -> None:
        assert await turn_repo.get("no-such") is None


class TestTransition:
    @pytest.mark.asyncio
    async def test_valid_transition(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        await turn_repo.transition(
            "t-1", from_state=TurnState.STARTED, to_state=TurnState.MEMORY_RETRIEVAL
        )
        got = await turn_repo.get("t-1")
        assert got is not None
        assert got.state is TurnState.MEMORY_RETRIEVAL

    @pytest.mark.asyncio
    async def test_invalid_from_state_raises(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        with pytest.raises(InvalidTurnTransition, match="state"):
            await turn_repo.transition(
                "t-1",
                from_state=TurnState.FINALISING,  # wrong
                to_state=TurnState.COMPLETED,
            )


class TestIncrementIteration:
    @pytest.mark.asyncio
    async def test_increments(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        await turn_repo.increment_iteration("t-1")
        await turn_repo.increment_iteration("t-1")
        got = await turn_repo.get("t-1")
        assert got is not None
        assert got.iteration_count == 2


class TestMarkCompleted:
    @pytest.mark.asyncio
    async def test_from_non_terminal(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        completed_at = datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC)
        await turn_repo.mark_completed(
            "t-1", stop_reason=StopReason.END_TURN, completed_at=completed_at
        )
        got = await turn_repo.get("t-1")
        assert got is not None
        assert got.state is TurnState.COMPLETED
        assert got.stop_reason is StopReason.END_TURN
        assert got.completed_at == completed_at

    @pytest.mark.asyncio
    async def test_rejects_from_terminal(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        completed_at = datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC)
        await turn_repo.mark_completed(
            "t-1", stop_reason=StopReason.END_TURN, completed_at=completed_at
        )
        with pytest.raises(InvalidTurnTransition):
            await turn_repo.mark_completed(
                "t-1", stop_reason=StopReason.END_TURN, completed_at=completed_at
            )


class TestMarkAborted:
    @pytest.mark.asyncio
    async def test_marks_aborted(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        await _seed(session_repo=session_repo, message_repo=message_repo)
        await turn_repo.insert(_record(started_at=started_at))
        completed_at = datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC)
        await turn_repo.mark_aborted("t-1", reason="user_cancel", completed_at=completed_at)
        got = await turn_repo.get("t-1")
        assert got is not None
        assert got.state is TurnState.ABORTED
        assert got.aborted_reason == "user_cancel"


class TestListNonTerminal:
    @pytest.mark.asyncio
    async def test_returns_only_non_terminal(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
        started_at: datetime,
    ) -> None:
        # Seed a session and three messages.
        await session_repo.insert(make_db_session(id="sess-1"))
        for i in range(3):
            await message_repo.append(make_db_message(id=f"msg-{i}", session_id="sess-1"))

        # Insert three turns: completed, aborted, in-flight.
        completed_at = datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC)
        await turn_repo.insert(
            _record(id="t-completed", message_id="msg-0", started_at=started_at)
        )
        await turn_repo.mark_completed(
            "t-completed", stop_reason=StopReason.END_TURN, completed_at=completed_at
        )
        await turn_repo.insert(_record(id="t-aborted", message_id="msg-1", started_at=started_at))
        await turn_repo.mark_aborted("t-aborted", reason="user_cancel", completed_at=completed_at)
        await turn_repo.insert(
            _record(
                id="t-inflight",
                message_id="msg-2",
                state=TurnState.PROVIDER_STREAMING,
                started_at=started_at,
            )
        )

        non_terminal = await turn_repo.list_non_terminal()
        assert {r.id for r in non_terminal} == {"t-inflight"}


class TestListForSession:
    @pytest.mark.asyncio
    async def test_lists_in_order(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        turn_repo: TurnRepo,
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-1"))
        for i in range(3):
            await message_repo.append(make_db_message(id=f"msg-{i}", session_id="sess-1"))

        for i in range(3):
            await turn_repo.insert(
                _record(
                    id=f"t-{i}",
                    message_id=f"msg-{i}",
                    started_at=datetime(2026, 4, 20, 12, i, 0, tzinfo=UTC),
                )
            )
        turns = await turn_repo.list_for_session("sess-1")
        assert [t.id for t in turns] == ["t-0", "t-1", "t-2"]
