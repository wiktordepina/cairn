"""Tests for ``DefaultToolRunner``.

Covers the per-call flow described in ``.plan/tool-system-design.md`` §7:
start → audit decision → (approve | reject) → execute → complete with
error classification on failure.

Runner is wired to real ``ToolCallRepo`` / ``ApprovalDecisionRepo`` over
a tmp-path SQLite DB so the lifecycle assertions are end-to-end.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import pytest
import pytest_asyncio

from cairn.domain import Message
from cairn.domain._content import TextBlock, ToolResultBlock, ToolUseBlock
from cairn.domain._enums import ErrorClass, SessionType, ToolCallStatus
from cairn.domain._sessions import Session
from cairn.orchestrator._enums import ApprovalOutcome
from cairn.orchestrator._middleware import ApprovalDecision
from cairn.persistence import (
    ApprovalDecisionRepo,
    MessageRepo,
    SessionRepo,
    ToolCallRepo,
)
from cairn.tools import DefaultToolRunner, PathEscape, SSRFBlocked, ToolError, ToolTimeout

if TYPE_CHECKING:
    from cairn.orchestrator import FrozenClock, TurnContext
    from cairn.persistence._connection import Database


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeTool:
    """Minimal ``Tool`` double with configurable invoke behaviour."""

    name: str = "fake_tool"
    description: str = "A tool for testing."
    input_schema: dict[str, object] = field(default_factory=lambda: {})
    tool_kind: Literal["native", "mcp", "delegation"] = "native"
    approval_required: bool = True
    risk_tier: int = 1
    side_effects: Literal["none", "read", "write"] = "read"
    timeout_s: float = 1.0
    result_text: str = "ok"
    raise_on_invoke: BaseException | None = None
    sleep_seconds: float = 0.0
    invoke_count: int = 0

    async def invoke(
        self,
        args: dict[str, object],  # noqa: ARG002
        ctx: object,  # noqa: ARG002
    ) -> ToolResultBlock:
        self.invoke_count += 1
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        if self.raise_on_invoke is not None:
            raise self.raise_on_invoke
        return ToolResultBlock(
            tool_use_id="",  # mirrors the @tool decorator convention
            content=self.result_text,
            is_error=False,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded(db: Database) -> tuple[str, str, str]:
    """Insert a session + assistant message so FK constraints hold.

    Returns ``(session_id, message_id, turn_id)``.
    """
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
    session = Session(
        id="sess-r",
        type=SessionType.COMPANION,
        persona="companion",
        model="x",
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )
    await SessionRepo(db).insert(session)
    msg = Message(role="assistant", session_id="sess-r")
    msg.content.append(TextBlock(text="x"))
    await MessageRepo(db).append(msg)
    return session.id, msg.id, "turn-1"


@pytest_asyncio.fixture
async def runner(db: Database, frozen_clock: FrozenClock) -> DefaultToolRunner:
    return DefaultToolRunner(
        tool_call_repo=ToolCallRepo(db),
        approval_repo=ApprovalDecisionRepo(db),
        clock=frozen_clock,
    )


def _tool_call(id_: str = "tc-1", **input_: object) -> ToolUseBlock:
    return ToolUseBlock(id=id_, name="fake_tool", input=dict(input_) or {"x": 1})


def _approve(decided_by: str = "auto:read-only") -> ApprovalDecision:
    return ApprovalDecision(outcome=ApprovalOutcome.APPROVE, decided_by=decided_by)


def _reject(reason: str = "user said no") -> ApprovalDecision:
    return ApprovalDecision(
        outcome=ApprovalOutcome.REJECT,
        decided_by="user",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Approved + successful invoke
# ---------------------------------------------------------------------------


class TestApprovedSuccess:
    @pytest.mark.asyncio
    async def test_inserts_pending_row_with_input_json(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        tc = _tool_call("tc-1", path="README.md")
        await runner.run(
            tool_call=tc,
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.tool_name == "fake_tool"
        assert record.input_json == '{"path": "README.md"}'

    @pytest.mark.asyncio
    async def test_records_approved_audit_row(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve("auto:read-only"),
            message_id=message_id,
        )
        rows = await ApprovalDecisionRepo(db).list_for_tool_call("tc-1")
        assert len(rows) == 1
        assert rows[0].decision == "approved"
        assert rows[0].decided_by == "auto:read-only"

    @pytest.mark.asyncio
    async def test_reaches_completed_state(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(result_text="hello"),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.status is ToolCallStatus.COMPLETED
        assert record.output_json is not None
        assert record.output_bytes is not None and record.output_bytes > 0

    @pytest.mark.asyncio
    async def test_returns_invoke_result(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        result = await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(result_text="42"),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert result.is_error is False
        assert result.content == "42"

    @pytest.mark.asyncio
    async def test_stamps_tool_use_id_when_blank(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        # FakeTool.invoke returns tool_use_id='will-be-overwritten';
        # the runner should replace that blank-convention value with
        # the real tool_call.id.
        result = await runner.run(
            tool_call=_tool_call("tc-stamp"),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert result.tool_use_id == "tc-stamp"

    @pytest.mark.asyncio
    async def test_invokes_tool_exactly_once(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        tool = FakeTool()
        await runner.run(
            tool_call=_tool_call(),
            tool=tool,
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert tool.invoke_count == 1


# ---------------------------------------------------------------------------
# Rejected
# ---------------------------------------------------------------------------


class TestRejected:
    @pytest.mark.asyncio
    async def test_sets_status_rejected(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_reject(),
            message_id=message_id,
        )
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.status is ToolCallStatus.REJECTED

    @pytest.mark.asyncio
    async def test_writes_rejected_audit_row(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_reject("not trusted"),
            message_id=message_id,
        )
        rows = await ApprovalDecisionRepo(db).list_for_tool_call("tc-1")
        assert len(rows) == 1
        assert rows[0].decision == "rejected"
        assert rows[0].reason == "not trusted"

    @pytest.mark.asyncio
    async def test_returns_error_block_with_reason(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        result = await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_reject("nope"),
            message_id=message_id,
        )
        assert result.is_error is True
        assert result.content == "nope"

    @pytest.mark.asyncio
    async def test_does_not_invoke_tool(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        tool = FakeTool()
        await runner.run(
            tool_call=_tool_call(),
            tool=tool,
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_reject(),
            message_id=message_id,
        )
        assert tool.invoke_count == 0


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_mark_timed_out_on_slow_tool(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        result = await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(timeout_s=0.01, sleep_seconds=10.0),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert result.is_error is True
        assert isinstance(result.content, str)
        assert "timed out" in result.content.lower()
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.status is ToolCallStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        PathEscape("escaped"),
        SSRFBlocked("blocked"),
        ToolTimeout("inner timeout"),
        ToolError("failed"),
    ],
)
class TestUserErrorClassification:
    @pytest.mark.asyncio
    async def test_maps_to_user_error_class(
        self,
        exc: BaseException,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        result = await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(raise_on_invoke=exc),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert result.is_error is True
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.status is ToolCallStatus.FAILED
        assert record.error_class is ErrorClass.USER


class TestUnexpectedErrorClassification:
    @pytest.mark.asyncio
    async def test_generic_exception_maps_to_unexpected(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        result = await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(raise_on_invoke=RuntimeError("boom")),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve(),
            message_id=message_id,
        )
        assert result.is_error is True
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.status is ToolCallStatus.FAILED
        assert record.error_class is ErrorClass.UNEXPECTED


# ---------------------------------------------------------------------------
# Approver narrowing
# ---------------------------------------------------------------------------


class TestApproverNarrowing:
    @pytest.mark.asyncio
    async def test_auto_prefix_narrows_to_auto(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve("auto:read-only"),
            message_id=message_id,
        )
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.approved_by == "auto"

    @pytest.mark.asyncio
    async def test_user_prefix_narrows_to_user(
        self,
        runner: DefaultToolRunner,
        seeded: tuple[str, str, str],
        db: Database,
        companion_session: Session,
        turn_ctx: TurnContext,
    ) -> None:
        _, message_id, turn_id = seeded
        await runner.run(
            tool_call=_tool_call(),
            tool=FakeTool(),
            session=companion_session.model_copy(update={"id": "sess-r"}),
            turn_id=turn_id,
            ctx=turn_ctx,
            decision=_approve("user:session-allowlist"),
            message_id=message_id,
        )
        record = await ToolCallRepo(db).get("tc-1")
        assert record is not None
        assert record.approved_by == "user"
