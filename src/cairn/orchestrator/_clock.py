"""Clock abstraction for testability.

Every timestamp that cairn records or depends on flows through ``Clock.now()``.
Tests substitute ``FrozenClock`` to get deterministic timestamps.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of time + cooperative sleep."""

    def now(self) -> datetime:
        """Return the current UTC datetime."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Cooperatively sleep for ``seconds``."""
        ...


class SystemClock:
    """Wall-clock time. The production implementation."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FrozenClock:
    """Controllable clock for tests.

    ``now()`` returns the frozen timestamp; ``advance(seconds)`` moves it
    forward; ``sleep()`` advances the clock instead of actually sleeping.
    """

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the frozen clock forward by ``seconds``."""
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        """Advance the clock; do not actually block."""
        self.advance(seconds)
