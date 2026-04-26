"""Tests for the `/cost` renderer."""

from __future__ import annotations

from cairn.persistence import CostSummary
from cairn.ui._cost_report import render_cost_summary


def _summary(**kw: float) -> CostSummary:
    base: dict[str, float] = {
        "session_usd": 0.04,
        "today_profile_usd": 0.41,
        "today_total_usd": 0.41,
        "last_3d_profile_usd": 1.28,
        "last_3d_total_usd": 1.28,
        "mtd_profile_usd": 5.67,
        "mtd_total_usd": 5.79,
    }
    base.update(kw)
    return CostSummary(**base)  # type: ignore[arg-type]


def test_renders_session_line_first() -> None:
    text = render_cost_summary(_summary())
    first = text.splitlines()[0]
    assert first.startswith("session cost: $")


def test_two_decimal_precision_throughout() -> None:
    text = render_cost_summary(_summary())
    assert "$0.04" in text
    assert "$0.41" in text
    assert "$5.67" in text
    assert "$5.79" in text
    # No 4-dp leftovers.
    assert "0.0400" not in text


def test_columns_present_with_labels() -> None:
    text = render_cost_summary(_summary())
    assert "current profile" in text
    assert "all profiles" in text
    assert "today" in text
    assert "last 3 days" in text
    assert "month-to-date" in text


def test_renders_zero_amounts_cleanly() -> None:
    text = render_cost_summary(_summary(session_usd=0.0, today_profile_usd=0.0))
    assert "$0.00" in text
