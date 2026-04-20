"""Tests for BasicCostTracker."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.config._models import BudgetConfig
from cairn.domain._enums import SessionType, UsageOperation
from cairn.domain._provider import UsageEvent
from cairn.domain._sessions import Session
from cairn.orchestrator import BasicCostTracker, BudgetVerdict, FrozenClock
from cairn.persistence._usage_repo import UsageRepo

if TYPE_CHECKING:
    from cairn.persistence._connection import Database
    from cairn.persistence._sessions_repo import SessionRepo


@pytest_asyncio.fixture
async def usage_repo(db: Database) -> UsageRepo:
    return UsageRepo(db)


async def _insert_session(repo: SessionRepo, session_id: str) -> None:
    """Helper: insert a minimal session so usage rows can reference it."""
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    await repo.insert(
        Session(
            id=session_id,
            type=SessionType.COMPANION,
            persona="companion",
            model="claude-opus-4-7",
            memory_space="companion",
            created_at=now,
            updated_at=now,
        )
    )


@pytest.fixture
def budgets() -> BudgetConfig:
    return BudgetConfig(per_turn_usd=0.50, per_session_usd=5.00, daily_usd=20.00)


@pytest_asyncio.fixture
async def tracker(
    usage_repo: UsageRepo, frozen_clock: FrozenClock, budgets: BudgetConfig
) -> BasicCostTracker:
    return BasicCostTracker(
        usage_repo=usage_repo, clock=frozen_clock, budgets=budgets
    )


def _usage(*, input_tokens: int = 100, output_tokens: int = 50) -> UsageEvent:
    return UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens)


class TestRecord:
    @pytest.mark.asyncio
    async def test_persists_with_turn_id(
        self,
        tracker: BasicCostTracker,
        session_repo: SessionRepo,
        usage_repo: UsageRepo,
    ) -> None:
        await _insert_session(session_repo, "sess-1")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="anthropic",
            model="claude-opus-4-7",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=250,
            cost_usd=0.12,
        )
        rows = await usage_repo.list_recent(session_id="sess-1")
        assert len(rows) == 1
        assert rows[0].turn_id == "t-1"
        assert rows[0].cost_usd == 0.12
        assert rows[0].operation is UsageOperation.PRIMARY_TURN

    @pytest.mark.asyncio
    async def test_cost_for_turn_sums(
        self,
        tracker: BasicCostTracker,
        session_repo: SessionRepo,
        usage_repo: UsageRepo,
    ) -> None:
        await _insert_session(session_repo, "sess-1")
        for _ in range(3):
            await tracker.record(
                session_id="sess-1",
                parent_session_id=None,
                turn_id="t-1",
                message_id=None,
                usage=_usage(),
                provider="x",
                model="y",
                role="primary",
                operation=UsageOperation.PRIMARY_TURN,
                duration_ms=10,
                cost_usd=0.10,
            )
        assert await usage_repo.cost_for_turn("t-1") == pytest.approx(0.30)


class TestShouldBlockTurn:
    @pytest.mark.asyncio
    async def test_proceeds_on_empty(self, tracker: BasicCostTracker) -> None:
        assert await tracker.should_block_turn(session_id="sess-1") == BudgetVerdict.PROCEED

    @pytest.mark.asyncio
    async def test_blocks_on_session_cap(
        self,
        tracker: BasicCostTracker,
        session_repo: SessionRepo,
    ) -> None:
        await _insert_session(session_repo, "sess-1")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="x",
            model="y",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=1,
            cost_usd=5.00,
        )
        assert await tracker.should_block_turn(session_id="sess-1") == BudgetVerdict.BLOCK

    @pytest.mark.asyncio
    async def test_warns_above_threshold(
        self, tracker: BasicCostTracker, session_repo: SessionRepo
    ) -> None:
        await _insert_session(session_repo, "sess-1")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="x",
            model="y",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=1,
            cost_usd=4.50,  # 90% of 5.00, above 0.8 * 5.00 = 4.00
        )
        assert await tracker.should_block_turn(session_id="sess-1") == BudgetVerdict.WARN

    @pytest.mark.asyncio
    async def test_proceeds_below_threshold(
        self, tracker: BasicCostTracker, session_repo: SessionRepo
    ) -> None:
        await _insert_session(session_repo, "sess-1")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="x",
            model="y",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=1,
            cost_usd=3.99,  # just below 0.8 * 5.00 = 4.00
        )
        assert await tracker.should_block_turn(session_id="sess-1") == BudgetVerdict.PROCEED

    @pytest.mark.asyncio
    async def test_blocks_on_daily_cap(
        self,
        usage_repo: UsageRepo,
        session_repo: SessionRepo,
        frozen_clock: FrozenClock,
        budgets: BudgetConfig,
    ) -> None:
        # Separate sessions, but combined daily cost exceeds cap.
        tracker = BasicCostTracker(
            usage_repo=usage_repo, clock=frozen_clock, budgets=budgets
        )
        for i in range(5):
            await _insert_session(session_repo, f"sess-{i}")
            await tracker.record(
                session_id=f"sess-{i}",
                parent_session_id=None,
                turn_id=f"t-{i}",
                message_id=None,
                usage=_usage(),
                provider="x",
                model="y",
                role="primary",
                operation=UsageOperation.PRIMARY_TURN,
                duration_ms=1,
                cost_usd=4.00,  # 5 * 4.00 = 20.00 daily cap
            )
        # A fresh session still blocks because the daily cap is hit.
        await _insert_session(session_repo, "sess-new")
        assert (
            await tracker.should_block_turn(session_id="sess-new")
            == BudgetVerdict.BLOCK
        )

    @pytest.mark.asyncio
    async def test_session_isolated(
        self, tracker: BasicCostTracker, session_repo: SessionRepo
    ) -> None:
        # Session 1 hits its cap, but session 2 is untouched.
        await _insert_session(session_repo, "sess-1")
        await _insert_session(session_repo, "sess-2")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="x",
            model="y",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=1,
            cost_usd=5.00,
        )
        assert await tracker.should_block_turn(session_id="sess-2") == BudgetVerdict.PROCEED

    @pytest.mark.asyncio
    async def test_uses_injected_clock_for_day_boundary(
        self,
        usage_repo: UsageRepo,
        session_repo: SessionRepo,
        budgets: BudgetConfig,
    ) -> None:
        # Record cost on day A.
        day_a = datetime(2026, 4, 20, 23, 59, 59, tzinfo=UTC)
        clock = FrozenClock(now=day_a)
        tracker = BasicCostTracker(
            usage_repo=usage_repo, clock=clock, budgets=budgets
        )
        await _insert_session(session_repo, "sess-1")
        await tracker.record(
            session_id="sess-1",
            parent_session_id=None,
            turn_id="t-1",
            message_id=None,
            usage=_usage(),
            provider="x",
            model="y",
            role="primary",
            operation=UsageOperation.PRIMARY_TURN,
            duration_ms=1,
            cost_usd=20.00,  # hits daily cap on day A
        )
        # Advance clock past midnight into day B.
        clock.advance(120)  # now 00:01:59 on next day
        # Daily cap resets (previous day's spend doesn't count today).
        # Session cap is session-specific, so sess-other proceeds cleanly.
        await _insert_session(session_repo, "sess-other")
        assert (
            await tracker.should_block_turn(session_id="sess-other")
            == BudgetVerdict.PROCEED
        )


class TestConstruction:
    def test_rejects_invalid_warn_fraction(
        self, usage_repo: UsageRepo, frozen_clock: FrozenClock, budgets: BudgetConfig
    ) -> None:
        with pytest.raises(ValueError, match="warn_threshold_fraction"):
            BasicCostTracker(
                usage_repo=usage_repo,
                clock=frozen_clock,
                budgets=budgets,
                warn_threshold_fraction=0,
            )
        with pytest.raises(ValueError, match="warn_threshold_fraction"):
            BasicCostTracker(
                usage_repo=usage_repo,
                clock=frozen_clock,
                budgets=budgets,
                warn_threshold_fraction=1.5,
            )
