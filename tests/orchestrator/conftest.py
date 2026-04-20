"""Shared fixtures for orchestrator tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.domain import Session
from cairn.domain._enums import SessionType
from cairn.orchestrator import FrozenClock
from cairn.persistence._connection import Database
from cairn.persistence._sessions_repo import SessionRepo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A deterministic clock pinned at 2026-04-20 12:00:00 UTC."""
    return FrozenClock(now=datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC))


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """Fresh on-disk DB per test, migrations applied on first connect."""
    database = Database(tmp_path / "cairn.db")
    yield database
    await database.close()


@pytest_asyncio.fixture
async def session_repo(db: Database) -> SessionRepo:
    return SessionRepo(db)


@pytest.fixture
def companion_session(frozen_clock: FrozenClock) -> Session:
    """A companion session with the default memory space."""
    now = frozen_clock.now()
    return Session(
        id="sess-companion-001",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        title=None,
        archived=False,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def ephemeral_session(frozen_clock: FrozenClock) -> Session:
    """An ephemeral session with no memory space."""
    now = frozen_clock.now()
    return Session(
        id="sess-ephemeral-001",
        type=SessionType.EPHEMERAL,
        persona="_ephemeral",
        model="gpt-4o-mini",
        memory_space=None,
        title=None,
        archived=False,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )
