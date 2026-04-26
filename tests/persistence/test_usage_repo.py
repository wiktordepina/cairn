"""Tests for UsageRepo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import StopReason, UsageOperation

from .conftest import make_db_session

if TYPE_CHECKING:
    from cairn.persistence._sessions_repo import SessionRepo
    from cairn.persistence._usage_repo import UsageRepo


async def _record(
    repo: UsageRepo,
    *,
    session_id: str | None = "sess-x",
    parent_session_id: str | None = None,
    cost: float = 0.10,
    model: str = "claude-opus-4-7",
    operation: UsageOperation = UsageOperation.PRIMARY_TURN,
    timestamp: datetime | None = None,
    metadata: dict[str, object] | None = None,
    profile: str | None = None,
) -> int:
    return await repo.record(
        timestamp=timestamp or datetime.now(UTC),
        session_id=session_id,
        message_id=None,
        parent_session_id=parent_session_id,
        provider="anthropic",
        model=model,
        role="primary",
        operation=operation,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        duration_ms=500,
        stop_reason=StopReason.END_TURN,
        metadata=metadata,
        profile=profile,
    )


class TestRecord:
    @pytest.mark.asyncio
    async def test_round_trip(self, session_repo: SessionRepo, usage_repo: UsageRepo) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, metadata={"retry_attempt": 0})
        records = await usage_repo.list_recent()
        assert len(records) == 1
        assert records[0].cost_usd == 0.10
        assert records[0].metadata == {"retry_attempt": 0}
        assert records[0].operation == UsageOperation.PRIMARY_TURN

    @pytest.mark.asyncio
    async def test_retries_are_separate_rows(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, metadata={"retry_attempt": 0})
        await _record(usage_repo, metadata={"retry_attempt": 1})
        records = await usage_repo.list_recent()
        assert len(records) == 2


class TestAggregations:
    @pytest.mark.asyncio
    async def test_total_cost_for_session(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, cost=0.10)
        await _record(usage_repo, cost=0.25)
        total = await usage_repo.total_cost_for_session("sess-x")
        assert abs(total - 0.35) < 1e-9

    @pytest.mark.asyncio
    async def test_total_cost_for_session_tree(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="parent"))
        await session_repo.insert(
            make_db_session(id="child", parent_session_id="parent", memory_space=None)
        )
        await _record(usage_repo, session_id="parent", cost=0.10)
        await _record(
            usage_repo,
            session_id="child",
            parent_session_id="parent",
            cost=0.20,
        )
        total = await usage_repo.total_cost_for_session_tree("parent")
        assert abs(total - 0.30) < 1e-9

    @pytest.mark.asyncio
    async def test_cost_in_window(self, session_repo: SessionRepo, usage_repo: UsageRepo) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        old = datetime.now(UTC) - timedelta(days=2)
        recent = datetime.now(UTC)
        await _record(usage_repo, cost=0.10, timestamp=old)
        await _record(usage_repo, cost=0.50, timestamp=recent)

        since = datetime.now(UTC) - timedelta(hours=1)
        windowed = await usage_repo.cost_in_window(since=since)
        assert abs(windowed - 0.50) < 1e-9

    @pytest.mark.asyncio
    async def test_by_model_in_window(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, cost=0.10, model="claude-opus-4-7")
        await _record(usage_repo, cost=0.05, model="claude-haiku-4-5")
        await _record(usage_repo, cost=0.15, model="claude-opus-4-7")

        now = datetime.now(UTC)
        result = await usage_repo.by_model_in_window(
            since=now - timedelta(hours=1), until=now + timedelta(hours=1)
        )
        assert abs(result["claude-opus-4-7"] - 0.25) < 1e-9
        assert abs(result["claude-haiku-4-5"] - 0.05) < 1e-9

    @pytest.mark.asyncio
    async def test_by_operation_in_window(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, cost=0.10, operation=UsageOperation.PRIMARY_TURN)
        await _record(usage_repo, cost=0.02, operation=UsageOperation.EXTRACTION)

        now = datetime.now(UTC)
        result = await usage_repo.by_operation_in_window(
            since=now - timedelta(hours=1), until=now + timedelta(hours=1)
        )
        assert abs(result[UsageOperation.PRIMARY_TURN] - 0.10) < 1e-9
        assert abs(result[UsageOperation.EXTRACTION] - 0.02) < 1e-9


class TestPerProfileAggregations:
    @pytest.mark.asyncio
    async def test_cost_in_window_by_profile_filters(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="sess-x"))
        await _record(usage_repo, cost=0.10, profile="personal")
        await _record(usage_repo, cost=0.20, profile="work")
        await _record(usage_repo, cost=0.05, profile=None)  # legacy NULL row

        since = datetime.now(UTC) - timedelta(hours=1)
        personal = await usage_repo.cost_in_window_by_profile(profile="personal", since=since)
        work = await usage_repo.cost_in_window_by_profile(profile="work", since=since)
        all_profiles = await usage_repo.cost_in_window(since=since)
        assert abs(personal - 0.10) < 1e-9
        assert abs(work - 0.20) < 1e-9
        # All-profiles total includes the NULL-profile legacy row.
        assert abs(all_profiles - 0.35) < 1e-9

    @pytest.mark.asyncio
    async def test_cost_summary_bundles_seven_aggregations(
        self, session_repo: SessionRepo, usage_repo: UsageRepo
    ) -> None:
        from zoneinfo import ZoneInfo

        await session_repo.insert(make_db_session(id="sess-x"))
        # `now` lives well past midnight local so the today and MTD
        # windows differ meaningfully from the 3-day window.
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 4, 26, 14, 0, tzinfo=UTC)
        await _record(  # today + 3d + mtd, current profile
            usage_repo,
            cost=0.10,
            profile="personal",
            timestamp=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        )
        await _record(  # today + 3d + mtd, other profile
            usage_repo,
            cost=0.07,
            profile="work",
            timestamp=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
        )
        await _record(  # 2 days back: 3d + mtd, current profile
            usage_repo,
            cost=0.50,
            profile="personal",
            timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=UTC),
        )
        await _record(  # earlier in month: mtd only, current profile
            usage_repo,
            cost=2.00,
            profile="personal",
            timestamp=datetime(2026, 4, 5, 14, 0, tzinfo=UTC),
        )

        summary = await usage_repo.cost_summary(
            session_id="sess-x", profile="personal", now=now, timezone=tz
        )
        assert abs(summary.session_usd - 2.67) < 1e-9
        assert abs(summary.today_profile_usd - 0.10) < 1e-9
        assert abs(summary.today_total_usd - 0.17) < 1e-9
        assert abs(summary.last_3d_profile_usd - 0.60) < 1e-9
        assert abs(summary.last_3d_total_usd - 0.67) < 1e-9
        assert abs(summary.mtd_profile_usd - 2.60) < 1e-9
        assert abs(summary.mtd_total_usd - 2.67) < 1e-9
