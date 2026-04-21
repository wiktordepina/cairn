"""Repository for the `turns` table.

One row per orchestrator turn. State transitions are conditional
UPDATEs — a state transition that finds the row in the wrong starting
state is a bug, surfaced via `InvalidTurnTransition` rather than
silently corrupting state.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from cairn.domain._enums import StopReason
from cairn.orchestrator._enums import TERMINAL_STATES, TurnState
from cairn.orchestrator._records import TurnRecord
from cairn.persistence._errors import PersistenceError

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database


_SELECT_COLS = (
    "id, session_id, user_message_id, state, iteration_count, model, "
    "started_at, completed_at, aborted_reason, stop_reason"
)


class InvalidTurnTransition(PersistenceError):
    """Attempted state transition did not match the row's current state."""


def _row_to_record(row: aiosqlite.Row) -> TurnRecord:
    return TurnRecord(
        id=row["id"],
        session_id=row["session_id"],
        user_message_id=row["user_message_id"],
        state=TurnState(row["state"]),
        iteration_count=row["iteration_count"],
        model=row["model"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=(
            datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
        ),
        aborted_reason=row["aborted_reason"],
        stop_reason=StopReason(row["stop_reason"]) if row["stop_reason"] else None,
    )


class TurnRepo:
    """Turn lifecycle persistence."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, turn: TurnRecord) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO turns (
                id, session_id, user_message_id, state, iteration_count, model,
                started_at, completed_at, aborted_reason, stop_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn.id,
                turn.session_id,
                turn.user_message_id,
                turn.state.value,
                turn.iteration_count,
                turn.model,
                turn.started_at.isoformat(),
                turn.completed_at.isoformat() if turn.completed_at else None,
                turn.aborted_reason,
                turn.stop_reason.value if turn.stop_reason else None,
            ),
        )
        await conn.commit()

    async def transition(
        self,
        turn_id: str,
        *,
        from_state: TurnState,
        to_state: TurnState,
    ) -> None:
        """Advance the turn's state. Raises `InvalidTurnTransition` if the
        row is not currently in `from_state`."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            "UPDATE turns SET state = ? WHERE id = ? AND state = ?",
            (to_state.value, turn_id, from_state.value),
        )
        await conn.commit()
        rowcount = cursor.rowcount
        await cursor.close()
        if rowcount != 1:
            raise InvalidTurnTransition(
                f"Turn {turn_id!r}: expected state {from_state.value!r} → "
                f"{to_state.value!r}, but row did not match."
            )

    async def increment_iteration(self, turn_id: str) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE turns SET iteration_count = iteration_count + 1 WHERE id = ?",
            (turn_id,),
        )
        await conn.commit()

    async def mark_completed(
        self,
        turn_id: str,
        *,
        stop_reason: StopReason,
        completed_at: datetime,
    ) -> None:
        """Transition to COMPLETED and record stop_reason + completed_at.

        Allowed from any non-terminal state (callers typically pass through
        `FINALISING` or `EXTRACTION_ENQUEUED` first)."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            UPDATE turns
            SET state = ?, completed_at = ?, stop_reason = ?
            WHERE id = ? AND state NOT IN (?, ?)
            """,
            (
                TurnState.COMPLETED.value,
                completed_at.isoformat(),
                stop_reason.value,
                turn_id,
                TurnState.COMPLETED.value,
                TurnState.ABORTED.value,
            ),
        )
        await conn.commit()
        rowcount = cursor.rowcount
        await cursor.close()
        if rowcount != 1:
            raise InvalidTurnTransition(
                f"Turn {turn_id!r}: cannot mark completed from terminal state."
            )

    async def mark_aborted(
        self,
        turn_id: str,
        *,
        reason: str,
        completed_at: datetime,
    ) -> None:
        """Transition to ABORTED. Allowed from any non-terminal state."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            UPDATE turns
            SET state = ?, completed_at = ?, aborted_reason = ?
            WHERE id = ? AND state NOT IN (?, ?)
            """,
            (
                TurnState.ABORTED.value,
                completed_at.isoformat(),
                reason,
                turn_id,
                TurnState.COMPLETED.value,
                TurnState.ABORTED.value,
            ),
        )
        await conn.commit()
        rowcount = cursor.rowcount
        await cursor.close()
        if rowcount != 1:
            raise InvalidTurnTransition(
                f"Turn {turn_id!r}: cannot mark aborted from terminal state."
            )

    async def get(self, turn_id: str) -> TurnRecord | None:
        conn = await self._db.connect()
        cursor = await conn.execute(f"SELECT {_SELECT_COLS} FROM turns WHERE id = ?", (turn_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_record(row) if row is not None else None

    async def list_non_terminal(self) -> list[TurnRecord]:
        """List turns whose state column is NOT one of the terminal values.

        Used on startup to find turns interrupted by a crash.
        """
        terminal_values = tuple(s.value for s in TERMINAL_STATES)
        placeholders = ",".join(["?"] * len(terminal_values))
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM turns
            WHERE state NOT IN ({placeholders})
            ORDER BY started_at ASC
            """,
            terminal_values,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]

    async def list_for_session(self, session_id: str) -> list[TurnRecord]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM turns
            WHERE session_id = ?
            ORDER BY started_at ASC
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]
