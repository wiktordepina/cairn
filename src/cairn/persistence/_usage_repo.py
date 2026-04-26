"""Repository for the `model_usage` table."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cairn.domain._enums import StopReason, UsageOperation
from cairn.persistence._records import CostSummary, UsageRecord

if TYPE_CHECKING:
    from datetime import tzinfo

    import aiosqlite

    from cairn.persistence._connection import Database

_SELECT_COLS = (
    "id, timestamp, session_id, message_id, turn_id, parent_session_id, "
    "provider, model, role, operation, "
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
    "cost_usd, duration_ms, stop_reason, is_error, metadata_json"
)


def _row_to_record(row: aiosqlite.Row) -> UsageRecord:
    metadata: dict[str, object] = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    return UsageRecord(
        id=row["id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        session_id=row["session_id"],
        message_id=row["message_id"],
        turn_id=row["turn_id"],
        parent_session_id=row["parent_session_id"],
        provider=row["provider"],
        model=row["model"],
        role=row["role"],
        operation=UsageOperation(row["operation"]),
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        cost_usd=row["cost_usd"],
        duration_ms=row["duration_ms"],
        stop_reason=StopReason(row["stop_reason"]) if row["stop_reason"] else None,
        is_error=bool(row["is_error"]),
        metadata=metadata,
    )


class UsageRepo:
    """Provider-call usage records and cost aggregations.

    One row per provider call (even on retry). Callers indicate retry status
    via `metadata={"retry_attempt": n, "previous_error": "..."}`.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        timestamp: datetime,
        session_id: str | None,
        message_id: str | None,
        parent_session_id: str | None,
        provider: str,
        model: str,
        role: str,
        operation: UsageOperation,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float,
        duration_ms: int | None = None,
        stop_reason: StopReason | None = None,
        is_error: bool = False,
        metadata: dict[str, object] | None = None,
        turn_id: str | None = None,
        profile: str | None = None,
    ) -> int:
        """Insert a usage row. Returns the new row id."""
        meta_json = json.dumps(metadata, sort_keys=True) if metadata else None
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            INSERT INTO model_usage (
                timestamp, session_id, message_id, turn_id, parent_session_id,
                provider, model, role, operation,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                cost_usd, duration_ms, stop_reason, is_error, metadata_json,
                profile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp.isoformat(),
                session_id,
                message_id,
                turn_id,
                parent_session_id,
                provider,
                model,
                role,
                operation.value,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                duration_ms,
                stop_reason.value if stop_reason else None,
                int(is_error),
                meta_json,
                profile,
            ),
        )
        await conn.commit()
        row_id = cursor.lastrowid
        await cursor.close()
        assert row_id is not None
        return row_id

    # -- Aggregations -----------------------------------------------------

    async def total_cost_for_session(self, session_id: str) -> float:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def total_cost_for_session_tree(self, session_id: str) -> float:
        """Sum the session's own cost plus the cost of all child sessions."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage
            WHERE session_id = ? OR parent_session_id = ?
            """,
            (session_id, session_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def cost_in_window(self, *, since: datetime, until: datetime | None = None) -> float:
        conditions = ["timestamp >= ?"]
        params: list[object] = [since.isoformat()]
        if until is not None:
            conditions.append("timestamp < ?")
            params.append(until.isoformat())
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage "
            f"WHERE {' AND '.join(conditions)}",
            params,
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def cost_today_utc(self) -> float:
        now = datetime.now(UTC)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.cost_in_window(since=midnight)

    async def cost_in_window_by_profile(
        self,
        *,
        profile: str,
        since: datetime,
        until: datetime | None = None,
    ) -> float:
        """Sum cost in [since, until) for *profile* only.

        Rows with NULL profile (recorded before this column existed)
        are excluded — they belong to "all profiles" totals only.
        """
        conditions = ["profile = ?", "timestamp >= ?"]
        params: list[object] = [profile, since.isoformat()]
        if until is not None:
            conditions.append("timestamp < ?")
            params.append(until.isoformat())
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage "
            f"WHERE {' AND '.join(conditions)}",
            params,
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def cost_summary(
        self,
        *,
        session_id: str,
        profile: str,
        now: datetime,
        timezone: tzinfo,
    ) -> CostSummary:
        """Bundle the seven aggregations rendered by ``/cost``.

        Windows:

        - ``today``        — since 00:00 local on *now*'s calendar day.
        - ``last_3d``      — rolling 72h ending at *now*.
        - ``mtd``          — since 00:00 local on the 1st of the
          current calendar month.

        Each window is reported twice: scoped to *profile* and across
        all profiles. The session-cost line is reported separately
        because it isn't time-windowed.

        SQLite stores ``timestamp`` as ISO-8601 UTC. Window boundaries
        are converted to UTC at query time so per-row TZ math stays
        out of the SQL.
        """
        local_now = now.astimezone(timezone)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_since = local_midnight.astimezone(UTC)
        last_3d_since = (now - timedelta(days=3)).astimezone(UTC)
        mtd_local = local_midnight.replace(day=1)
        mtd_since = mtd_local.astimezone(UTC)

        results = await asyncio.gather(
            self.total_cost_for_session(session_id),
            self.cost_in_window_by_profile(profile=profile, since=today_since),
            self.cost_in_window(since=today_since),
            self.cost_in_window_by_profile(profile=profile, since=last_3d_since),
            self.cost_in_window(since=last_3d_since),
            self.cost_in_window_by_profile(profile=profile, since=mtd_since),
            self.cost_in_window(since=mtd_since),
        )
        return CostSummary(
            session_usd=results[0],
            today_profile_usd=results[1],
            today_total_usd=results[2],
            last_3d_profile_usd=results[3],
            last_3d_total_usd=results[4],
            mtd_profile_usd=results[5],
            mtd_total_usd=results[6],
        )

    async def cost_per_turn(self, message_id: str) -> float:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def most_recent_primary_turn(self, session_id: str) -> UsageRecord | None:
        """Return the most recent `PRIMARY_TURN` usage row for a session.

        Used by the UI's `/context` command to surface the last
        prompt's token footprint. Returns None when the session has
        no recorded primary-turn activity yet.
        """
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM model_usage "
            "WHERE session_id = ? AND operation = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (session_id, UsageOperation.PRIMARY_TURN.value),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_record(row) if row is not None else None

    async def cost_for_turn(self, turn_id: str) -> float:
        """Sum all provider calls attributed to a single orchestrator turn.

        Includes every iteration in the tool loop plus any delegation
        sub-calls that carry the same `turn_id`.
        """
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage WHERE turn_id = ?",
            (turn_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return float(row[0]) if row is not None else 0.0

    async def by_operation_in_window(
        self, *, since: datetime, until: datetime
    ) -> dict[UsageOperation, float]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT operation, COALESCE(SUM(cost_usd), 0.0)
            FROM model_usage
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY operation
            """,
            (since.isoformat(), until.isoformat()),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {UsageOperation(r[0]): float(r[1]) for r in rows}

    async def by_model_in_window(self, *, since: datetime, until: datetime) -> dict[str, float]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT model, COALESCE(SUM(cost_usd), 0.0)
            FROM model_usage
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY model
            """,
            (since.isoformat(), until.isoformat()),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {r[0]: float(r[1]) for r in rows}

    async def list_recent(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[UsageRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM model_usage {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_record(r) for r in rows]
