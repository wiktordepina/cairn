"""Tests for the Clock abstraction."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cairn.orchestrator import Clock, FrozenClock, SystemClock


class TestFrozenClock:
    def test_now_is_frozen(self) -> None:
        ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
        clock = FrozenClock(now=ts)
        assert clock.now() == ts
        assert clock.now() == ts  # stable across calls

    def test_advance_moves_forward(self) -> None:
        clock = FrozenClock(now=datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC))
        clock.advance(60)
        assert clock.now() == datetime(2026, 4, 20, 10, 1, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_sleep_advances_without_blocking(self) -> None:
        clock = FrozenClock(now=datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC))
        await clock.sleep(30.5)
        assert clock.now() == datetime(2026, 4, 20, 10, 0, 30, 500_000, tzinfo=UTC)

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            FrozenClock(now=datetime(2026, 4, 20, 10, 0, 0))  # noqa: DTZ001


class TestSystemClock:
    def test_now_is_utc(self) -> None:
        clock = SystemClock()
        now = clock.now()
        assert now.tzinfo is not None

    def test_satisfies_clock_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)
        assert isinstance(
            FrozenClock(now=datetime(2026, 4, 20, tzinfo=UTC)), Clock
        )
