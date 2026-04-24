"""Plain-text rendering of the `/context` budget summary.

Pure functions so the formatter can be unit-tested without the
Textual event loop. The handler in `_commands.py` wraps the
output in a muted `Banner` and mounts it in the chat log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.persistence import UsageRecord


@dataclass(frozen=True, slots=True)
class ContextReportInput:
    """Bundle the `/context` command needs to render its banner.

    The bootstrap-supplied `context_source` callback returns this;
    test harnesses hand in a constant instance so the handler's
    formatter path is exercised without a live orchestrator.
    """

    context_window: int | None
    model: str
    last_usage: UsageRecord | None


def format_context_report(report: ContextReportInput) -> str:
    """Render the `/context` banner body for the current session.

    Three shapes the output takes:

    - `context_window` unknown → fall back to "context window not
      reported by this model".
    - `context_window` known, `last_usage` None → "budget only"
      shape showing the cap with a "no usage yet" line.
    - Both present → usage / budget, cache split, percent used.

    A trailing placeholder line notes that per-section breakdown
    (identity, user_context, memory_index, etc.) is planned but
    not in V1 — the stacked-bar follow-up depends on
    `ContextManager` exposing segment sizes.
    """
    context_window = report.context_window
    model = report.model
    last_usage = report.last_usage

    if context_window is None or context_window <= 0:
        return f"context: window size not reported for model {model!r}."

    header = [f"context: model={model}, budget={_fmt(context_window)} tokens"]

    if last_usage is None:
        header.append("  no primary-turn usage recorded yet.")
        header.append("  (segment breakdown unavailable in V1)")
        return "\n".join(header)

    used = last_usage.input_tokens + last_usage.cache_read_tokens + last_usage.cache_write_tokens
    pct = _percent(used, context_window)

    header[0] = (
        f"context: {_fmt(used)} / {_fmt(context_window)} tokens ({pct}% used) — model={model}"
    )
    header.append(
        f"  cached: {_fmt(last_usage.cache_read_tokens)} (read) + "
        f"{_fmt(last_usage.cache_write_tokens)} (write)"
    )
    header.append(f"  fresh:  {_fmt(last_usage.input_tokens)}")
    header.append(f"  output: {_fmt(last_usage.output_tokens)} (this turn)")
    header.append("  (segment breakdown unavailable in V1)")
    return "\n".join(header)


def _fmt(n: int) -> str:
    """Thousands-grouped integer for readability."""
    return f"{n:,}"


def _percent(used: int, total: int) -> int:
    """Clamp to 0..100 integer — values outside that range are noise
    from future cache-hit heuristics and shouldn't explode the banner."""
    if total <= 0:
        return 0
    raw = int(round(100 * used / total))
    return max(0, min(100, raw))
