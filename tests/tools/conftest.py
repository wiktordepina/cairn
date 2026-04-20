"""Shared fixtures for tool-system tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.domain import Session
from cairn.domain._enums import SessionType
from cairn.orchestrator import FrozenClock, TurnContext
from cairn.persistence._connection import Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(now=datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def companion_session(frozen_clock: FrozenClock) -> Session:
    now = frozen_clock.now()
    return Session(
        id="sess-c",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def persona_session(frozen_clock: FrozenClock) -> Session:
    now = frozen_clock.now()
    return Session(
        id="sess-p",
        type=SessionType.PERSONA,
        persona="work-assistant",
        model="claude-opus-4-7",
        memory_space=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def ephemeral_session(frozen_clock: FrozenClock) -> Session:
    now = frozen_clock.now()
    return Session(
        id="sess-e",
        type=SessionType.EPHEMERAL,
        persona="_ephemeral",
        model="gpt-4o-mini",
        memory_space=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def turn_ctx(companion_session: Session) -> TurnContext:
    return TurnContext(session=companion_session, turn_id="t-1", iteration=0)


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "cairn.db")
    yield database
    await database.close()
