"""Tests for the `now` built-in tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.orchestrator import FrozenClock
from cairn.tools.builtin._now import make_now

if TYPE_CHECKING:
    from cairn.orchestrator import TurnContext


class TestMetadata:
    def test_tier_zero_no_approval(self) -> None:
        clock = FrozenClock(now=datetime(2026, 4, 26, 12, 0, tzinfo=UTC))
        t = make_now(clock)
        assert t.name == "now"
        assert t.risk_tier == 0
        assert t.side_effects == "none"
        assert t.approval_required is False


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_iso_and_tz(self, turn_ctx: TurnContext) -> None:
        clock = FrozenClock(now=datetime(2026, 4, 26, 14, 30, tzinfo=UTC))
        t = make_now(clock, timezone_name="Europe/London")
        result = await t.invoke({}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        payload = json.loads(content)
        assert payload["tz"] == "Europe/London"
        # 14:30 UTC on 2026-04-26 → 15:30 BST (UK summer time, +01:00).
        parsed = datetime.fromisoformat(payload["iso"])
        assert parsed.year == 2026
        assert parsed.month == 4
        assert parsed.day == 26
        assert parsed.hour == 15
        assert parsed.minute == 30

    @pytest.mark.asyncio
    async def test_unknown_timezone_falls_back_to_local(self, turn_ctx: TurnContext) -> None:
        clock = FrozenClock(now=datetime(2026, 4, 26, 12, 0, tzinfo=UTC))
        t = make_now(clock, timezone_name="Mars/Phobos")
        result = await t.invoke({}, turn_ctx)
        payload = json.loads(result.content)  # type: ignore[arg-type]
        # Tool should still return *something* parseable rather than
        # crash; tz label is whatever the host supplies.
        parsed = datetime.fromisoformat(payload["iso"])
        assert parsed.year == 2026
