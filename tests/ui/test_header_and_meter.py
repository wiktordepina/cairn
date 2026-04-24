"""Pilot tests for the session header + cost meter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
from textual.color import Color

from cairn.domain import BudgetWarning, SessionType
from cairn.domain._sessions import Session
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._theme import DEFAULT_SESSION_TYPE_COLOURS
from cairn.ui._widgets import CostMeter, SessionHeader, SessionTypeBadge

if TYPE_CHECKING:
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


def _persona_session() -> Session:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    return Session(
        id="sess-p",
        type=SessionType.PERSONA,
        persona="writing-coach",
        model="claude-sonnet-4-6",
        memory_space="writing-coach",
        title="Essay on fen walks",
        created_at=now,
        updated_at=now,
    )


class TestSessionHeader:
    @pytest.mark.asyncio
    async def test_header_renders_badge_for_companion_session(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            header = screen.query_one(SessionHeader)
            badge = header.query_one(SessionTypeBadge)
            assert badge.session_type is SessionType.COMPANION
            expected = Color.parse(DEFAULT_SESSION_TYPE_COLOURS[SessionType.COMPANION])
            assert badge.styles.background == expected

    @pytest.mark.asyncio
    async def test_header_shows_persona_accent_and_title(self) -> None:
        session = _persona_session()
        app = _app_for(session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            header = screen.query_one(SessionHeader)
            badge = header.query_one(SessionTypeBadge)
            assert badge.session_type is SessionType.PERSONA
            expected = Color.parse(DEFAULT_SESSION_TYPE_COLOURS[SessionType.PERSONA])
            assert badge.styles.background == expected
            # Title flows through into the label (not queried directly —
            # plain-text substring suffices).
            labels = [str(label.renderable) for label in header.query("Label")]
            assert any("Essay on fen walks" in line for line in labels)

    @pytest.mark.asyncio
    async def test_header_untitled_placeholder(self, companion_session: Session) -> None:
        untitled = companion_session.model_copy(update={"title": None})
        app = _app_for(untitled)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            header = screen.query_one(SessionHeader)
            labels = [str(label.renderable) for label in header.query("Label")]
            assert any("(untitled)" in line for line in labels)


class TestCostMeter:
    @pytest.mark.asyncio
    async def test_meter_initial_state_is_zero(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            meter = screen.query_one(CostMeter)
            assert meter.cost_usd == 0.0
            assert "-warning" not in meter.classes

    @pytest.mark.asyncio
    async def test_budget_warning_updates_meter_with_warning(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)
        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                BudgetWarning(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    cost_usd=4.20,
                    threshold_usd=4.00,
                )
            )
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            meter = screen.query_one(CostMeter)
            assert meter.cost_usd == pytest.approx(4.20)
            assert "-warning" in meter.classes
            assert "$4.2000" in str(meter.renderable)
