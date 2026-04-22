"""Shared fixtures for memory-brick tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest_asyncio

from cairn.domain._content import TextBlock
from cairn.domain._enums import SessionType
from cairn.domain._messages import Message
from cairn.domain._sessions import Session
from cairn.persistence._connection import Database
from cairn.persistence._messages_repo import MessageRepo
from cairn.persistence._sessions_repo import SessionRepo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """Fresh on-disk DB per test, migrations applied on first connect."""
    database = Database(tmp_path / "cairn.db")
    yield database
    await database.close()


@pytest_asyncio.fixture
async def seed_session(db: Database) -> tuple[str, str]:
    """Insert a companion session + one assistant message; return (session_id, message_id).

    Extractor / queue tests reference these IDs so the memory_entries
    foreign keys are satisfied.
    """
    now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    session = Session(
        id="sess-1",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )
    await SessionRepo(db).insert(session)
    msg = Message(id="msg-1", role="assistant", session_id="sess-1")
    msg.content.append(TextBlock(text="assistant response"))
    await MessageRepo(db).append(msg)
    return session.id, msg.id
