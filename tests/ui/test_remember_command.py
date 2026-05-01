"""Pilot tests for `/remember`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio

from cairn.config._models import (
    BudgetConfig,
    ConventionFilesConfig,
    MemoryConfig,
    ProfileConfig,
    UIConfig,
)
from cairn.domain._enums import MemoryClass, MemoryEntryType, SessionType
from cairn.domain._sessions import Session
from cairn.persistence._connection import Database
from cairn.persistence._memory_repo import MemoryRepo
from cairn.persistence._sessions_repo import SessionRepo
from cairn.ui._app import CairnApp
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


def _make_profile(tmp_path: Path, *, importance: int = 7) -> ProfileConfig:
    soul = tmp_path / "soul.md"
    user = tmp_path / "user.md"
    mem = tmp_path / "MEMORY.md"
    for p in (soul, user, mem):
        p.write_text("# stub\n", encoding="utf-8")
    return ProfileConfig(
        primary_model="claude-opus-4-7",
        utility_model="claude-opus-4-7",
        soul_document_path=soul,
        user_context_path=user,
        memory_md_path=mem,
        budgets=BudgetConfig(),
        convention_files=ConventionFilesConfig(),
        memory=MemoryConfig(explicit_remember_importance=importance),
        ui=UIConfig(),
    )


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "cairn.db")
    yield database
    await database.close()


async def _persist(db_: Database, session: Session) -> None:
    await SessionRepo(db_).insert(session)


def _app(
    session: Session,
    *,
    repo: MemoryRepo | None = None,
    profile: ProfileConfig | None = None,
) -> CairnApp:
    return CairnApp(
        orchestrator=cast("Orchestrator", _OrchStub()),
        session=session,
        memory_repo=repo,
        profile=profile,
    )


def _banners_text(screen: SessionScreen) -> str:
    banners = list(screen.query_one(ChatLog).query(Banner))
    return "\n".join(str(b.renderable) for b in banners)


class TestRememberDispatch:
    @pytest.mark.asyncio
    async def test_empty_text_shows_usage_hint(self, db: Database) -> None:
        repo = MemoryRepo(db)
        app = _app(_make_session(), repo=repo)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/remember"
            await bar.action_submit()
            await pilot.pause()
            assert "usage: /remember" in _banners_text(screen)

    @pytest.mark.asyncio
    async def test_no_memory_repo_shows_unwired_banner(self) -> None:
        app = _app(_make_session(), repo=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/remember a fact"
            await bar.action_submit()
            await pilot.pause()
            assert "did not wire memory" in _banners_text(screen)

    @pytest.mark.asyncio
    async def test_persists_with_default_importance_seven(self, db: Database) -> None:
        repo = MemoryRepo(db)
        sess = _make_session()
        await _persist(db, sess)
        app = _app(sess, repo=repo)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/remember Wiktor takes coffee with no sugar."
            await bar.action_submit()
            await pilot.pause()

            hits = await repo.search(
                memory_space="companion",
                query="coffee",
                k=5,
            )
            assert len(hits) == 1
            assert hits[0].entry.entry_type == MemoryEntryType.FACT
            assert hits[0].entry.memory_class == MemoryClass.SEMANTIC
            # No profile wired → default = 7
            assert hits[0].entry.importance == 7

    @pytest.mark.asyncio
    async def test_uses_profile_importance_override(self, db: Database, tmp_path: Path) -> None:
        repo = MemoryRepo(db)
        profile = _make_profile(tmp_path, importance=9)
        sess = _make_session()
        await _persist(db, sess)
        app = _app(sess, repo=repo, profile=profile)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/remember favourite colour is blue"
            await bar.action_submit()
            await pilot.pause()

            hits = await repo.search(
                memory_space="companion",
                query="colour",
                k=5,
            )
            assert hits[0].entry.importance == 9

    @pytest.mark.asyncio
    async def test_dedup_hit_does_not_double_insert(self, db: Database) -> None:
        repo = MemoryRepo(db)
        sess = _make_session()
        await _persist(db, sess)
        app = _app(sess, repo=repo)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/remember Wiktor prefers oat milk in coffee."
            await bar.action_submit()
            await pilot.pause()
            bar.value = "/remember Wiktor prefers oat milk in coffee."
            await bar.action_submit()
            await pilot.pause()

            hits = await repo.search(
                memory_space="companion",
                query="oat milk coffee",
                k=5,
            )
            # Dedup: only one row even after two `/remember`s.
            assert len(hits) == 1
