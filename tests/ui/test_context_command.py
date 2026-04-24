"""Unit + pilot tests for `/context` and its rendering helper."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain._enums import StopReason, UsageOperation
from cairn.persistence import UsageRecord
from cairn.ui._app import CairnApp
from cairn.ui._context_report import ContextReportInput, format_context_report
from cairn.ui._widgets import Banner, ChatLog, CommandBar

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> UsageRecord:
    return UsageRecord(
        id=1,
        timestamp=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        session_id="sess-1",
        message_id="msg-1",
        turn_id="t-1",
        provider="anthropic",
        model="claude-opus-4-7",
        role="primary",
        operation=UsageOperation.PRIMARY_TURN,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=0.05,
        stop_reason=StopReason.END_TURN,
    )


# ---------------------------------------------------------------------------
# Unit: format_context_report
# ---------------------------------------------------------------------------


class TestFormatContextReport:
    def test_unknown_window_falls_back(self) -> None:
        out = format_context_report(
            ContextReportInput(context_window=None, model="mystery-1", last_usage=None)
        )
        assert "window size not reported" in out
        assert "mystery-1" in out

    def test_zero_window_falls_back(self) -> None:
        out = format_context_report(
            ContextReportInput(context_window=0, model="weird", last_usage=None)
        )
        assert "window size not reported" in out

    def test_budget_only_when_no_usage(self) -> None:
        out = format_context_report(
            ContextReportInput(context_window=200_000, model="m", last_usage=None)
        )
        assert "200,000" in out
        assert "no primary-turn usage" in out
        assert "segment breakdown unavailable" in out

    def test_full_render_with_usage(self) -> None:
        usage = _usage(
            input_tokens=5_000,
            output_tokens=800,
            cache_read=10_000,
            cache_write=2_000,
        )
        out = format_context_report(
            ContextReportInput(context_window=200_000, model="claude-opus-4-7", last_usage=usage)
        )
        # used = 5_000 + 10_000 + 2_000 = 17_000
        assert "17,000 / 200,000" in out
        # 17_000 / 200_000 = 8.5% → rounds to 8
        assert "(8% used)" in out
        assert "10,000 (read)" in out
        assert "2,000 (write)" in out
        assert "5,000" in out  # fresh
        assert "800" in out  # output
        assert "claude-opus-4-7" in out

    def test_percent_clamped_to_100(self) -> None:
        usage = _usage(input_tokens=500_000)
        out = format_context_report(
            ContextReportInput(context_window=200_000, model="m", last_usage=usage)
        )
        assert "(100% used)" in out

    def test_percent_zero_with_empty_usage(self) -> None:
        usage = _usage()  # all zero tokens
        out = format_context_report(
            ContextReportInput(context_window=200_000, model="m", last_usage=usage)
        )
        assert "(0% used)" in out


# ---------------------------------------------------------------------------
# Pilot: /context handler
# ---------------------------------------------------------------------------


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


class TestContextCommand:
    @pytest.mark.asyncio
    async def test_context_with_source_renders_banner(self, companion_session: Session) -> None:
        usage = _usage(input_tokens=1_500, output_tokens=400, cache_read=12_000)
        report = ContextReportInput(
            context_window=200_000, model="claude-opus-4-7", last_usage=usage
        )

        async def _src() -> ContextReportInput:
            return report

        app = CairnApp(
            orchestrator=cast("Orchestrator", Mock()),
            session=companion_session,
            context_source=_src,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/context"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "13,500 / 200,000" in rendered
            assert "claude-opus-4-7" in rendered

    @pytest.mark.asyncio
    async def test_context_without_source_shows_placeholder(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/context"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            assert any(
                "bootstrap did not wire a context source" in str(b.renderable) for b in banners
            )

    @pytest.mark.asyncio
    async def test_context_source_error_renders_warning(self, companion_session: Session) -> None:
        async def _src() -> ContextReportInput:
            raise RuntimeError("db down")

        app = CairnApp(
            orchestrator=cast("Orchestrator", Mock()),
            session=companion_session,
            context_source=_src,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/context"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "failed to load" in rendered
            # Kind is warning, not error.
            warning_banners = [b for b in banners if b.kind == "warning"]
            assert len(warning_banners) == 1
