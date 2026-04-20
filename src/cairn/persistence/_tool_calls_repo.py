"""Repository for the ``tool_calls`` table — lifecycle-aware."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from cairn.domain._enums import ErrorClass, ToolCallStatus
from cairn.persistence._errors import InvalidToolCallTransition
from cairn.persistence._records import ToolCallRecord

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database

_SELECT_COLS = (
    "id, session_id, message_id, tool_name, tool_kind, server, "
    "input_json, input_truncated, output_json, output_truncated, output_bytes, "
    "status, is_error, error_class, error_message, "
    "is_delegation, delegation_session_id, approval_required, "
    "approved_by, approved_at, started_at, completed_at, duration_ms"
)


def _opt_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row: aiosqlite.Row) -> ToolCallRecord:
    return ToolCallRecord(
        id=row["id"],
        session_id=row["session_id"],
        message_id=row["message_id"],
        tool_name=row["tool_name"],
        tool_kind=row["tool_kind"],
        server=row["server"],
        input_json=row["input_json"],
        input_truncated=bool(row["input_truncated"]),
        output_json=row["output_json"],
        output_truncated=bool(row["output_truncated"]),
        output_bytes=row["output_bytes"],
        status=ToolCallStatus(row["status"]),
        is_error=bool(row["is_error"]),
        error_class=ErrorClass(row["error_class"]) if row["error_class"] else None,
        error_message=row["error_message"],
        is_delegation=bool(row["is_delegation"]),
        delegation_session_id=row["delegation_session_id"],
        approval_required=bool(row["approval_required"]),
        approved_by=row["approved_by"],
        approved_at=_opt_dt(row["approved_at"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=_opt_dt(row["completed_at"]),
        duration_ms=row["duration_ms"],
    )


class ToolCallRepo:
    """Tool call lifecycle persistence.

    Each transition method enforces the allowed previous status; an attempt
    to transition from a disallowed state raises ``InvalidToolCallTransition``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def start(
        self,
        *,
        id: str,
        session_id: str,
        message_id: str,
        tool_name: str,
        tool_kind: Literal["native", "mcp", "delegation"],
        server: str | None = None,
        input_json: str,
        input_truncated: bool = False,
        approval_required: bool = False,
        is_delegation: bool = False,
        started_at: datetime | None = None,
    ) -> None:
        """Insert a new tool call in the PENDING state."""
        ts = started_at or datetime.now(UTC)
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO tool_calls (
                id, session_id, message_id, tool_name, tool_kind, server,
                input_json, input_truncated, status,
                is_delegation, approval_required, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                session_id,
                message_id,
                tool_name,
                tool_kind,
                server,
                input_json,
                int(input_truncated),
                ToolCallStatus.PENDING.value,
                int(is_delegation),
                int(approval_required),
                ts.isoformat(),
            ),
        )
        await conn.commit()

    async def _transition(
        self,
        tool_call_id: str,
        *,
        new_status: ToolCallStatus,
        allowed_prev: tuple[ToolCallStatus, ...],
        extra_sets: dict[str, object] | None = None,
    ) -> None:
        sets = ["status = ?"]
        params: list[object] = [new_status.value]
        if extra_sets:
            for col, value in extra_sets.items():
                sets.append(f"{col} = ?")
                params.append(value)
        params.append(tool_call_id)
        prev_placeholders = ", ".join("?" for _ in allowed_prev)
        params.extend(s.value for s in allowed_prev)
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"UPDATE tool_calls SET {', '.join(sets)} "
            f"WHERE id = ? AND status IN ({prev_placeholders})",
            params,
        )
        await conn.commit()
        if cursor.rowcount == 0:
            raise InvalidToolCallTransition(
                f"Cannot transition tool call {tool_call_id!r} to {new_status.value!r}; "
                f"required previous status in {[s.value for s in allowed_prev]}"
            )

    async def approve(
        self,
        tool_call_id: str,
        *,
        approved_by: Literal["user", "auto"],
        approved_at: datetime | None = None,
    ) -> None:
        ts = (approved_at or datetime.now(UTC)).isoformat()
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.APPROVED,
            allowed_prev=(ToolCallStatus.PENDING,),
            extra_sets={"approved_by": approved_by, "approved_at": ts},
        )

    async def reject(self, tool_call_id: str, *, reason: str | None = None) -> None:
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.REJECTED,
            allowed_prev=(ToolCallStatus.PENDING,),
            extra_sets={"error_message": reason} if reason else None,
        )

    async def mark_executing(self, tool_call_id: str) -> None:
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.EXECUTING,
            allowed_prev=(ToolCallStatus.PENDING, ToolCallStatus.APPROVED),
        )

    async def complete(
        self,
        tool_call_id: str,
        *,
        output_json: str,
        output_bytes: int,
        output_truncated: bool = False,
        completed_at: datetime | None = None,
        delegation_session_id: str | None = None,
    ) -> None:
        ts = completed_at or datetime.now(UTC)
        # Look up started_at to compute duration_ms.
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT started_at FROM tool_calls WHERE id = ?", (tool_call_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        duration_ms: int | None = None
        if row is not None:
            started = datetime.fromisoformat(row["started_at"])
            duration_ms = int((ts - started).total_seconds() * 1000)

        extra: dict[str, object] = {
            "output_json": output_json,
            "output_bytes": output_bytes,
            "output_truncated": int(output_truncated),
            "completed_at": ts.isoformat(),
        }
        if duration_ms is not None:
            extra["duration_ms"] = duration_ms
        if delegation_session_id is not None:
            extra["delegation_session_id"] = delegation_session_id

        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.COMPLETED,
            allowed_prev=(ToolCallStatus.EXECUTING,),
            extra_sets=extra,
        )

    async def mark_failed(
        self,
        tool_call_id: str,
        *,
        error_class: ErrorClass,
        error_message: str | None = None,
    ) -> None:
        ts = datetime.now(UTC).isoformat()
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.FAILED,
            allowed_prev=(ToolCallStatus.EXECUTING, ToolCallStatus.APPROVED),
            extra_sets={
                "is_error": 1,
                "error_class": error_class.value,
                "error_message": error_message,
                "completed_at": ts,
            },
        )

    async def mark_timed_out(self, tool_call_id: str) -> None:
        ts = datetime.now(UTC).isoformat()
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.TIMED_OUT,
            allowed_prev=(ToolCallStatus.EXECUTING,),
            extra_sets={"completed_at": ts},
        )

    async def mark_cancelled(self, tool_call_id: str) -> None:
        ts = datetime.now(UTC).isoformat()
        await self._transition(
            tool_call_id,
            new_status=ToolCallStatus.CANCELLED,
            allowed_prev=(
                ToolCallStatus.PENDING,
                ToolCallStatus.APPROVED,
                ToolCallStatus.EXECUTING,
            ),
            extra_sets={"completed_at": ts},
        )

    async def get(self, tool_call_id: str) -> ToolCallRecord | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM tool_calls WHERE id = ?", (tool_call_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_record(row) if row is not None else None

    async def list_for_session(
        self,
        session_id: str,
        *,
        status: ToolCallStatus | None = None,
        limit: int = 50,
    ) -> list[ToolCallRecord]:
        conditions = ["session_id = ?"]
        params: list[object] = [session_id]
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        params.append(limit)
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM tool_calls
            WHERE {" AND ".join(conditions)}
            ORDER BY started_at ASC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]

    async def list_pending_approval(self, session_id: str) -> list[ToolCallRecord]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM tool_calls
            WHERE session_id = ? AND status = ? AND approval_required = 1
            ORDER BY started_at ASC
            """,
            (session_id, ToolCallStatus.PENDING.value),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]
