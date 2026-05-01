"""Pilot tests for `/recall` and the `MemoryRecallModal`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio

from cairn.config._models import MemoryConfig
from cairn.domain._enums import MemoryClass, MemoryEntryType, SessionType
from cairn.domain._sessions import Session
from cairn.memory._retrieval import MemoryService
from cairn.orchestrator._clock import FrozenClock
from cairn.persistence._connection import Database
from cairn.persistence._memory_repo import MemoryRepo
from cairn.ui._app import CairnApp
from cairn.ui._screens import MemoryRecallModal
from cairn.ui._widgets import Banner, ChatLog, CommandBar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from cairn.orchestrator import Orchestrator
    from cairn.ui._screens import SessionScreen


NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


class _OrchStub:
    pass


def _make_session() -> Session:
    return Session(
        id="sess-1",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        created_at=NOW,
        updated_at=NOW,
    )


def _make_persona_session() -> Session:
    return Session(
        id="sess-p",
        type=SessionType.PERSONA,
        persona="work",
        model="claude-opus-4-7",
        memory_space=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "cairn.db")
    yield database
    await database.close()


def _service(db_: Database) -> tuple[MemoryService, MemoryRepo]:
    repo = MemoryRepo(db_)
    svc = MemoryService(
        memory_repo=repo,
        clock=FrozenClock(now=NOW),
        memory_config=MemoryConfig(),
    )
    return svc, repo


def _app(session: Session, *, service: MemoryService | None = None) -> CairnApp:
    return CairnApp(
        orchestrator=cast("Orchestrator", _OrchStub()),
        session=session,
        memory_service=service,
    )


def _banners_text(screen: SessionScreen) -> str:
    banners = list(screen.query_one(ChatLog).query(Banner))
    return "\n".join(str(b.renderable) for b in banners)


class TestRecallDispatch:
    @pytest.mark.asyncio
    async def test_empty_query_shows_usage_hint(self, db: Database) -> None:
        svc, _ = _service(db)
        app = _app(_make_session(), service=svc)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/recall"
            await bar.action_submit()
            await pilot.pause()
            assert "usage: /recall" in _banners_text(screen)

    @pytest.mark.asyncio
    async def test_no_memory_service_shows_unwired_banner(self) -> None:
        app = _app(_make_session(), service=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/recall coffee"
            await bar.action_submit()
            await pilot.pause()
            assert "did not wire memory" in _banners_text(screen)

    @pytest.mark.asyncio
    async def test_session_without_memory_space_shows_banner(self, db: Database) -> None:
        svc, _ = _service(db)
        app = _app(_make_persona_session(), service=svc)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/recall coffee"
            await bar.action_submit()
            await pilot.pause()
            assert "no memory_space" in _banners_text(screen)

    @pytest.mark.asyncio
    async def test_recall_opens_modal_with_hits(self, db: Database) -> None:
        svc, repo = _service(db)
        await repo.store(
            memory_space="companion",
            content="Wiktor takes coffee with no sugar, oat milk, single shot.",
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=8,
        )
        app = _app(_make_session(), service=svc)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/recall coffee"
            await bar.action_submit()
            # Worker spins up the modal asynchronously.
            await pilot.pause()
            await pilot.pause()
            # The modal is now the top screen.
            top = app.screen
            assert isinstance(top, MemoryRecallModal)
            await pilot.press("escape")
            await pilot.pause()


class TestMemoryRecallModalRender:
    @pytest.mark.asyncio
    async def test_empty_hits_shows_no_match_label(self) -> None:
        app = _app(_make_session())
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = MemoryRecallModal(query="nothing", hits=[])
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import Label

            text = "\n".join(str(label.renderable) for label in modal.query(Label))
            assert "no memories matched" in text

    @pytest.mark.asyncio
    async def test_hits_render_score_and_full_content(self, db: Database) -> None:
        svc, repo = _service(db)
        long_text = "Wiktor takes coffee with no sugar. " * 20
        entry = await repo.store(
            memory_space="companion",
            content=long_text,
            entry_type=MemoryEntryType.PREFERENCE,
            memory_class=MemoryClass.SEMANTIC,
            importance=9,
        )
        scored = await svc.retrieve_scored(space="companion", query="coffee", k=5)
        assert scored
        app = _app(_make_session())
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = MemoryRecallModal(query="coffee", hits=scored)
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import Static

            rendered = "\n".join(str(w.renderable) for w in modal.query(Static))
            assert "preference" in rendered
            assert "imp=9" in rendered
            assert f"#{entry.id}" in rendered
            # Full content visible (not truncated).
            assert long_text in rendered
