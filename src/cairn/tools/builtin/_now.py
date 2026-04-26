"""`now` — Tier-0 read-only tool returning the current ISO-8601 timestamp.

The system prompt carries a cacheable date stamp (see
`StandardContextManager._render_today`); minute-precision time is
deliberately excluded there because it staleens within minutes and
breaks the prompt cache. This tool covers cases where the model
needs the wall-clock — log analysis, scheduling, "exactly how long
ago" questions.

Construction takes an optional `timezone_name` so the same field
that drives the date stamp also drives the tool's output, keeping
both sources of "what time is it for the user" consistent.
"""

from __future__ import annotations

import json
from datetime import tzinfo  # noqa: TCH003 — used in runtime helper
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from cairn.tools._decorator import tool

if TYPE_CHECKING:
    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._clock import Clock
    from cairn.orchestrator._protocols import Tool


class NowArgs(BaseModel):
    """No inputs — the tool always returns the current time."""


def make_now(
    clock: Clock,
    *,
    timezone_name: str | None = None,
) -> Tool:
    """Build a `now` tool bound to *clock* and the profile's timezone.

    Args:
        clock: Clock providing `now()`. The orchestrator's clock is
            the right choice in production; tests can pass a
            `FrozenClock`.
        timezone_name: IANA name (e.g. ``"Europe/London"``). When
            ``None``, the result uses the host's local timezone.
    """
    # Resolve the tzinfo eagerly so a misconfigured profile fails
    # at bootstrap rather than on the first turn.
    tz = _resolve_timezone(timezone_name)

    @tool(
        name="now",
        description=(
            "Return the current date and time as ISO-8601 with timezone "
            "offset and IANA name. Use when minute-precision matters "
            "(scheduling, log reading, exact-duration questions). The "
            "system prompt already carries today's date, so prefer that "
            "for date-only references."
        ),
        risk_tier=0,
        side_effects="none",
        timeout_s=2.0,
        args_model=NowArgs,
    )
    async def now(_args: NowArgs, _ctx: TurnContext) -> str:
        moment = clock.now().astimezone(tz) if tz is not None else clock.now().astimezone()
        tz_label = timezone_name or (str(moment.tzinfo) if moment.tzinfo else "local")
        return json.dumps({"iso": moment.isoformat(), "tz": tz_label})

    return now


def _resolve_timezone(name: str | None) -> tzinfo | None:
    if name is None:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None


__all__ = ["NowArgs", "make_now"]
