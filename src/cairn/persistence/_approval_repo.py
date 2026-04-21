"""Repository for the ``approval_decisions`` table.

One row per approval event. Populated by the tool-system's
``DefaultToolRunner`` each time an approval decision is made
(auto-approved, user-approved, rejected). Provides an audit trail for
the security-doc §6 requirement (timestamp, tool, args, approved-by).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from cairn.orchestrator._records import ApprovalDecisionRecord

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database


_SELECT_COLS = "id, tool_call_id, decided_at, decided_by, decision, reason, args_snapshot_json"


def _row_to_record(row: aiosqlite.Row) -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        id=row["id"],
        tool_call_id=row["tool_call_id"],
        decided_at=datetime.fromisoformat(row["decided_at"]),
        decided_by=row["decided_by"],
        decision=row["decision"],
        reason=row["reason"],
        args_snapshot_json=row["args_snapshot_json"],
    )


class ApprovalDecisionRepo:
    """Writes + reads the ``approval_decisions`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        tool_call_id: str,
        decided_at: datetime,
        decided_by: str,
        decision: Literal["approved", "rejected"],
        reason: str | None = None,
        args_snapshot_json: str | None = None,
    ) -> int:
        """Insert a new decision row. Returns the auto-assigned ``id``."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            INSERT INTO approval_decisions (
                tool_call_id, decided_at, decided_by, decision, reason, args_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tool_call_id,
                decided_at.isoformat(),
                decided_by,
                decision,
                reason,
                args_snapshot_json,
            ),
        )
        await conn.commit()
        row_id = cursor.lastrowid
        await cursor.close()
        assert row_id is not None
        return row_id

    async def list_for_tool_call(self, tool_call_id: str) -> list[ApprovalDecisionRecord]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM approval_decisions
            WHERE tool_call_id = ?
            ORDER BY decided_at ASC
            """,
            (tool_call_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]
