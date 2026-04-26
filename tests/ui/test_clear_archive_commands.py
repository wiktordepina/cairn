"""Pilot tests for `/clear` and `/archive`.

Both commands replace the Tranche-1 `/new` stub. `/clear` archives
the current session and opens a fresh one of the same type +
persona; `/archive` opens an inform-and-confirm modal then exits
the app on accept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from cairn.domain._enums import SessionType
from cairn.domain._sessions import Session
from cairn.ui._app import CairnApp
from cairn.ui._widgets import Banner, ChatLog, CommandBar

if TYPE_CHECKING:
    from cairn.orchestrator import Orchestrator


def _make_session(model: str = "claude-opus-4-7", session_id: str = "sess-1") -> Session:
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    return Session(
        id=session_id,
        type=SessionType.COMPANION,
        persona="companion",
        model=model,
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )


class _OrchStub:
    """Minimal Orchestrator stand-in for `/clear` and `/archive`."""

    def __init__(self) -> None:
        self.archived: list[str] = []
        self.started: list[Session] = []
        self._counter = 0

    async def archive_session(self, session_id: str) -> None:
        self.archived.append(session_id)

    async def start_session(
        self, *, type: SessionType, persona: str, model: str | None = None
    ) -> Session:
        del model
        self._counter += 1
        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        new = Session(
            id=f"sess-new-{self._counter}",
            type=type,
            persona=persona,
            model="claude-opus-4-7",
            memory_space="companion",
            created_at=now,
            updated_at=now,
        )
        self.started.append(new)
        return new


def _app_with_orch(orch: _OrchStub, session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", orch), session=session)


class TestClearCommand:
    @pytest.mark.asyncio
    async def test_archives_and_opens_fresh_session(self) -> None:
        orch = _OrchStub()
        original = _make_session(session_id="orig-1")
        app = _app_with_orch(orch, original)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            assert screen.session.id == "orig-1"

            bar = screen.query_one(CommandBar)
            bar.value = "/clear"
            await bar.action_submit()
            await pilot.pause()

            assert orch.archived == ["orig-1"]
            assert len(orch.started) == 1
            assert screen.session.id != "orig-1"

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "session cleared" in rendered

    @pytest.mark.asyncio
    async def test_refuses_mid_turn(self) -> None:
        orch = _OrchStub()
        app = _app_with_orch(orch, _make_session())
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            from cairn.ui._widgets import ActivityIndicator

            screen.query_one(ActivityIndicator).set_streaming()
            assert screen.is_turn_active() is True

            bar = screen.query_one(CommandBar)
            bar.value = "/clear"
            await bar.action_submit()
            await pilot.pause()

            assert orch.archived == []
            assert orch.started == []
            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "busy" in rendered


class TestArchiveCommand:
    @pytest.mark.asyncio
    async def test_archive_confirmed_archives_and_quits(self) -> None:
        orch = _OrchStub()
        original = _make_session(session_id="orig-1")
        app = _app_with_orch(orch, original)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/archive"
            await bar.action_submit()
            await pilot.pause()
            # Confirm via hotkey on the choice modal.
            await pilot.press("a")
            await pilot.pause()

            assert orch.archived == ["orig-1"]

    @pytest.mark.asyncio
    async def test_archive_cancelled_no_op(self) -> None:
        orch = _OrchStub()
        original = _make_session(session_id="orig-1")
        app = _app_with_orch(orch, original)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/archive"
            await bar.action_submit()
            await pilot.pause()
            # Decline via Esc.
            await pilot.press("escape")
            await pilot.pause()

            assert orch.archived == []
            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "archive cancelled" in rendered
