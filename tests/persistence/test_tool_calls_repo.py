"""Tests for ToolCallRepo."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import ErrorClass, ToolCallStatus
from cairn.persistence._errors import InvalidToolCallTransition

from .conftest import make_db_message, make_db_session

if TYPE_CHECKING:
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._sessions_repo import SessionRepo
    from cairn.persistence._tool_calls_repo import ToolCallRepo


@pytest.fixture
def setup_session_and_message(
    session_repo: SessionRepo, message_repo: MessageRepo
) -> tuple[str, str]:
    """Returns (session_id, message_id) after seeding both."""
    return ("sess-x", "msg-x")


async def _seed(session_repo: SessionRepo, message_repo: MessageRepo) -> tuple[str, str]:
    await session_repo.insert(make_db_session(id="sess-x"))
    msg = make_db_message(session_id="sess-x", id="msg-x")
    await message_repo.append(msg)
    return "sess-x", "msg-x"


async def _start(repo: ToolCallRepo, **kw: object) -> str:
    defaults: dict[str, object] = {
        "id": "tc-1",
        "session_id": "sess-x",
        "message_id": "msg-x",
        "tool_name": "fetch",
        "tool_kind": "native",
        "input_json": '{"url":"x"}',
    }
    defaults.update(kw)
    await repo.start(**defaults)  # type: ignore[arg-type]
    return str(defaults["id"])


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_full_happy_path(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo)
        await tool_call_repo.approve("tc-1", approved_by="auto")
        await tool_call_repo.mark_executing("tc-1")
        await tool_call_repo.complete("tc-1", output_json='{"ok":true}', output_bytes=12)
        record = await tool_call_repo.get("tc-1")
        assert record is not None
        assert record.status == ToolCallStatus.COMPLETED
        assert record.completed_at is not None
        assert record.duration_ms is not None and record.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo)
        # Skip approve+execute, try to complete directly
        with pytest.raises(InvalidToolCallTransition):
            await tool_call_repo.complete("tc-1", output_json='{"x":1}', output_bytes=8)

    @pytest.mark.asyncio
    async def test_reject_blocks_approve(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo)
        await tool_call_repo.reject("tc-1", reason="user said no")
        with pytest.raises(InvalidToolCallTransition):
            await tool_call_repo.approve("tc-1", approved_by="user")

    @pytest.mark.asyncio
    async def test_mark_failed_from_executing(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo)
        await tool_call_repo.approve("tc-1", approved_by="auto")
        await tool_call_repo.mark_executing("tc-1")
        await tool_call_repo.mark_failed(
            "tc-1", error_class=ErrorClass.TRANSIENT, error_message="timeout"
        )
        record = await tool_call_repo.get("tc-1")
        assert record is not None
        assert record.status == ToolCallStatus.FAILED
        assert record.is_error is True
        assert record.error_class == ErrorClass.TRANSIENT


class TestQueries:
    @pytest.mark.asyncio
    async def test_list_pending_approval(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo, id="tc-need", approval_required=True)
        await _start(tool_call_repo, id="tc-auto", approval_required=False)

        pending = await tool_call_repo.list_pending_approval("sess-x")
        assert {r.id for r in pending} == {"tc-need"}

    @pytest.mark.asyncio
    async def test_list_for_session_filtered(
        self,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        tool_call_repo: ToolCallRepo,
    ) -> None:
        await _seed(session_repo, message_repo)
        await _start(tool_call_repo, id="tc-1")
        await _start(tool_call_repo, id="tc-2")
        await tool_call_repo.reject("tc-2")

        pending = await tool_call_repo.list_for_session("sess-x", status=ToolCallStatus.PENDING)
        rejected = await tool_call_repo.list_for_session("sess-x", status=ToolCallStatus.REJECTED)
        assert {r.id for r in pending} == {"tc-1"}
        assert {r.id for r in rejected} == {"tc-2"}
