"""Tests for ApprovalDecisionRepo."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from cairn.domain import Message
from cairn.domain._content import TextBlock
from cairn.domain._enums import SessionType
from cairn.domain._sessions import Session
from cairn.persistence import (
    ApprovalDecisionRepo,
    MessageRepo,
    SessionRepo,
    ToolCallRepo,
)

if TYPE_CHECKING:
    from cairn.persistence._connection import Database


@pytest_asyncio.fixture
async def approval_repo(db: Database) -> ApprovalDecisionRepo:
    return ApprovalDecisionRepo(db)


@pytest_asyncio.fixture
async def tool_call_id(db: Database) -> str:
    """Insert a minimal chain of rows so a tool_call can be referenced
    from approval_decisions without tripping FK constraints."""
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    session = Session(
        id="sess-1",
        type=SessionType.COMPANION,
        persona="companion",
        model="x",
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )
    await SessionRepo(db).insert(session)
    msg = Message(role="assistant", session_id="sess-1")
    msg.content.append(TextBlock(text="x"))
    await MessageRepo(db).append(msg)
    await ToolCallRepo(db).start(
        id="tc-1",
        session_id="sess-1",
        message_id=msg.id,
        tool_name="file_read",
        tool_kind="native",
        input_json='{"path": "README.md"}',
        approval_required=True,
        started_at=now,
    )
    return "tc-1"


class TestRecord:
    @pytest.mark.asyncio
    async def test_inserts_approved_row(
        self, approval_repo: ApprovalDecisionRepo, tool_call_id: str
    ) -> None:
        row_id = await approval_repo.record(
            tool_call_id=tool_call_id,
            decided_at=datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC),
            decided_by="user",
            decision="approved",
            reason=None,
            args_snapshot_json='{"path": "README.md"}',
        )
        assert row_id > 0
        rows = await approval_repo.list_for_tool_call(tool_call_id)
        assert len(rows) == 1
        assert rows[0].decision == "approved"
        assert rows[0].decided_by == "user"
        assert rows[0].reason is None

    @pytest.mark.asyncio
    async def test_inserts_rejected_row_with_reason(
        self, approval_repo: ApprovalDecisionRepo, tool_call_id: str
    ) -> None:
        await approval_repo.record(
            tool_call_id=tool_call_id,
            decided_at=datetime(2026, 4, 20, 12, 1, 0, tzinfo=UTC),
            decided_by="user",
            decision="rejected",
            reason="not trusted",
            args_snapshot_json=None,
        )
        rows = await approval_repo.list_for_tool_call(tool_call_id)
        assert rows[0].decision == "rejected"
        assert rows[0].reason == "not trusted"


class TestListForToolCall:
    @pytest.mark.asyncio
    async def test_empty_for_unknown(
        self, approval_repo: ApprovalDecisionRepo
    ) -> None:
        assert await approval_repo.list_for_tool_call("nope") == []

    @pytest.mark.asyncio
    async def test_returns_in_time_order(
        self, approval_repo: ApprovalDecisionRepo, tool_call_id: str
    ) -> None:
        base = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        # Insert two decisions.
        await approval_repo.record(
            tool_call_id=tool_call_id,
            decided_at=base.replace(minute=2),
            decided_by="auto:read-only",
            decision="approved",
        )
        await approval_repo.record(
            tool_call_id=tool_call_id,
            decided_at=base.replace(minute=1),  # earlier timestamp
            decided_by="user",
            decision="rejected",
            reason="bad",
        )
        rows = await approval_repo.list_for_tool_call(tool_call_id)
        assert len(rows) == 2
        # ORDER BY decided_at ASC: rejected (minute 1) first.
        assert rows[0].decision == "rejected"
        assert rows[1].decision == "approved"
