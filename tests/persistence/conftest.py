"""Shared fixtures for persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest_asyncio

from cairn.domain._content import TextBlock
from cairn.domain._messages import Message
from cairn.domain._sessions import Session
from cairn.persistence._connection import Database
from cairn.persistence._messages_repo import MessageRepo
from cairn.persistence._sessions_repo import SessionRepo
from cairn.persistence._tool_calls_repo import ToolCallRepo
from cairn.persistence._usage_repo import UsageRepo

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
async def session_repo(db: Database) -> SessionRepo:
    return SessionRepo(db)


@pytest_asyncio.fixture
async def message_repo(db: Database) -> MessageRepo:
    return MessageRepo(db)


@pytest_asyncio.fixture
async def tool_call_repo(db: Database) -> ToolCallRepo:
    return ToolCallRepo(db)


@pytest_asyncio.fixture
async def usage_repo(db: Database) -> UsageRepo:
    return UsageRepo(db)


def make_db_session(
    *,
    id: str = "sess-test",
    type: str = "companion",
    persona: str = "companion",
    model: str = "claude-opus-4-7",
    memory_space: str | None = "companion",
    **overrides: Any,
) -> Session:
    """Build a Session with sensible defaults for testing."""
    now = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": id,
        "type": type,
        "persona": persona,
        "model": model,
        "memory_space": memory_space,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Session(**base)


def make_db_message(
    *,
    session_id: str = "sess-test",
    role: str = "user",
    text: str | None = "hi",
    **overrides: Any,
) -> Message:
    """Build a Message with a single TextBlock for testing."""
    msg = Message(role=role, session_id=session_id, **overrides)  # type: ignore[arg-type]
    if text is not None:
        msg.content.append(TextBlock(text=text))
    return msg
