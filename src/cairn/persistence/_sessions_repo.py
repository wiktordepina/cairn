"""Repository for the `sessions` table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cairn.domain._enums import SessionType
from cairn.domain._sessions import Session

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database


# Sentinel for "field not provided" so callers can distinguish None
# (set to NULL) from omission.
class _Unset:
    pass


_UNSET = _Unset()


_SELECT_COLS = (
    "id, type, persona, model, memory_space, title, archived, "
    "parent_session_id, created_at, updated_at"
)


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        type=SessionType(row["type"]),
        persona=row["persona"],
        model=row["model"],
        memory_space=row["memory_space"],
        title=row["title"],
        archived=bool(row["archived"]),
        parent_session_id=row["parent_session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class SessionRepo:
    """CRUD for sessions."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, session: Session) -> None:
        conn = await self._db.connect()
        await conn.execute(
            """
            INSERT INTO sessions
                (id, type, persona, model, memory_space, title, archived,
                 parent_session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.type.value,
                session.persona,
                session.model,
                session.memory_space,
                session.title,
                int(session.archived),
                session.parent_session_id,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await conn.commit()

    async def get(self, session_id: str) -> Session | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_session(row) if row is not None else None

    async def update_metadata(
        self,
        session_id: str,
        *,
        title: str | None | _Unset = _UNSET,
        archived: bool | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        """Update mutable metadata. Auto-bumps `updated_at` if omitted."""
        sets: list[str] = []
        params: list[Any] = []
        if not isinstance(title, _Unset):
            sets.append("title = ?")
            params.append(title)
        if archived is not None:
            sets.append("archived = ?")
            params.append(int(archived))
        ts = updated_at if updated_at is not None else datetime.now(UTC)
        sets.append("updated_at = ?")
        params.append(ts.isoformat())
        params.append(session_id)

        conn = await self._db.connect()
        await conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
        await conn.commit()

    async def list_for_space(
        self,
        memory_space: str | None,
        *,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        """List sessions in a memory space.

        `memory_space=None` matches memoryless sessions only.
        """
        archived_clause = "" if include_archived else "AND archived = 0"
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM sessions
            WHERE memory_space IS ? {archived_clause}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (memory_space, limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_session(r) for r in rows]

    async def list_recent(
        self,
        *,
        type: SessionType | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Session]:
        """List recently-updated sessions across **all** memory spaces."""
        conditions: list[str] = []
        params: list[Any] = []
        if type is not None:
            conditions.append("type = ?")
            params.append(type.value)
        if not include_archived:
            conditions.append("archived = 0")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM sessions
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_session(r) for r in rows]

    async def children_of(self, parent_session_id: str) -> list[Session]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS} FROM sessions
            WHERE parent_session_id = ?
            ORDER BY created_at ASC
            """,
            (parent_session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_session(r) for r in rows]

    async def archive(self, session_id: str) -> None:
        await self.update_metadata(session_id, archived=True)
