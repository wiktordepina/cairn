"""Render a `CostSummary` as a multi-line banner.

`/cost` calls `UsageRepo.cost_summary` and hands the bundle here for
formatting. Two-decimal precision throughout — reads as money, not
sub-cent micro-amounts. The session line is shown separately because
it isn't time-windowed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.persistence._records import CostSummary


_ROW_LABELS: tuple[str, ...] = ("today", "last 3 days", "month-to-date")


def render_cost_summary(summary: CostSummary, *, precision: int = 2) -> str:
    """Format the summary as a fixed-width banner string."""
    rows: tuple[tuple[str, float, float], ...] = (
        ("today", summary.today_profile_usd, summary.today_total_usd),
        ("last 3 days", summary.last_3d_profile_usd, summary.last_3d_total_usd),
        ("month-to-date", summary.mtd_profile_usd, summary.mtd_total_usd),
    )
    label_width = max(len(label) for label, *_ in rows)
    money_width = _money_width(summary, precision)

    header_label_pad = " " * label_width
    header = (
        f"{header_label_pad}  {'current profile':>{money_width}}  {'all profiles':>{money_width}}"
    )
    body_lines = [
        f"{label:<{label_width}}  "
        f"{_money(profile_amt, precision):>{money_width}}  "
        f"{_money(total_amt, precision):>{money_width}}"
        for label, profile_amt, total_amt in rows
    ]
    session_line = f"session cost: {_money(summary.session_usd, precision)}"
    return "\n".join([session_line, "", header, *body_lines])


def _money(value: float, precision: int) -> str:
    return f"${value:.{precision}f}"


def _money_width(summary: CostSummary, precision: int) -> int:
    candidates = (
        summary.session_usd,
        summary.today_profile_usd,
        summary.today_total_usd,
        summary.last_3d_profile_usd,
        summary.last_3d_total_usd,
        summary.mtd_profile_usd,
        summary.mtd_total_usd,
    )
    longest = max(len(_money(v, precision)) for v in candidates)
    return max(longest, len("current profile"), len("all profiles"))


__all__ = ["render_cost_summary"]
