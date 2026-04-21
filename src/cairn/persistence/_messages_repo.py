"""Repository for the ``messages`` table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from cairn.domain._messages import Message

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database


_SELECT_COLS = "id, session_id, idx, role, content_json, created_at, turn_id"


def _row_to_message(row: aiosqlite.Row) -> Message:
    role: Literal["user", "assistant"] = row["role"]
    return Message.from_content_json(
        id=row["id"],
        session_id=row["session_id"],
        idx=row["idx"],
        role=role,
        content_json=row["content_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class MessageRepo:
    """Append-only message storage."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, message: Message, *, turn_id: str | None = None) -> None:
        """Append a message, computing its idx server-side.

        Mutates ``message.idx`` in place. ``turn_id`` threads orchestrator
        turn attribution onto the row; None for messages not produced
        inside an orchestrator turn (e.g. imports).
        """
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(idx), -1) + 1 FROM messages WHERE session_id = ?",
                (message.session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            next_idx = int(row[0]) if row is not None else 0
            message.idx = next_idx
            content = message.content_json().decode("utf-8")
            await conn.execute(
                f"INSERT INTO messages ({_SELECT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message.id,
                    message.session_id,
                    message.idx,
                    message.role,
                    content,
                    message.created_at.isoformat(),
                    turn_id,
                ),
            )

    async def insert_with_idx(self, message: Message, *, turn_id: str | None = None) -> None:
        """Insert a message with its existing ``idx`` (for replay/import)."""
        conn = await self._db.connect()
        content = message.content_json().decode("utf-8")
        await conn.execute(
            f"INSERT INTO messages ({_SELECT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.session_id,
                message.idx,
                message.role,
                content,
                message.created_at.isoformat(),
                turn_id,
            ),
        )
        await conn.commit()

    async def get(self, message_id: str) -> Message | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM messages WHERE id = ?", (message_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_message(row) if row is not None else None

    async def list_for_session(
        self,
        session_id: str,
        *,
        after_idx: int | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        conditions = ["session_id = ?"]
        params: list[object] = [session_id]
        if after_idx is not None:
            conditions.append("idx > ?")
            params.append(after_idx)
        where = " AND ".join(conditions)
        limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""

        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM messages
            WHERE {where}
            ORDER BY idx ASC{limit_clause}
            """,
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_message(r) for r in rows]

    async def count_for_session(self, session_id: str) -> int:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row is not None else 0

    async def replace_content(self, message_id: str, content_json: bytes) -> None:
        """Replace stored content (used for redaction passes only)."""
        conn = await self._db.connect()
        await conn.execute(
            "UPDATE messages SET content_json = ? WHERE id = ?",
            (content_json.decode("utf-8"), message_id),
        )
        await conn.commit()
