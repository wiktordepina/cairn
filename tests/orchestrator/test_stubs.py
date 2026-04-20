"""Tests for the default stub implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.orchestrator import (
    ApprovalOutcome,
    ApprovalRequest,
    AutoApproveGateway,
    DenyAllGateway,
    EmptyToolRegistry,
    MinimalContextManager,
    NullExtractionQueue,
    NullMemoryService,
    RaisingToolRunner,
)

if TYPE_CHECKING:
    from cairn.domain import Session


def _approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        tool_call_id="tc-1",
        tool_name="fetch",
        args={"url": "https://example.com"},
        risk_tier=2,
        side_effects="read",
    )


class TestNullMemoryService:
    @pytest.mark.asyncio
    async def test_returns_empty(self) -> None:
        svc = NullMemoryService()
        got = await svc.retrieve(space="companion", query="anything", k=8)
        assert got == []


class TestNullExtractionQueue:
    def test_submit_is_noop(self) -> None:
        q = NullExtractionQueue()
        q.submit(session_id="s", since_idx=0, turn_id="t")


class TestEmptyToolRegistry:
    def test_for_session_returns_empty(self, companion_session: Session) -> None:
        reg = EmptyToolRegistry()
        assert reg.for_session(companion_session) == []

    def test_get_returns_none(self) -> None:
        reg = EmptyToolRegistry()
        assert reg.get("anything") is None


class TestRaisingToolRunner:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        runner = RaisingToolRunner()
        with pytest.raises(NotImplementedError, match="tool system brick"):
            await runner.run()


class TestAutoApproveGateway:
    @pytest.mark.asyncio
    async def test_approves(self) -> None:
        gate = AutoApproveGateway()
        decision = await gate.request(
            session_id="s", tool_call_id="tc-1", request=_approval_request()
        )
        assert decision.outcome is ApprovalOutcome.APPROVE
        assert decision.decided_by == "auto:stub"


class TestDenyAllGateway:
    @pytest.mark.asyncio
    async def test_rejects(self) -> None:
        gate = DenyAllGateway()
        decision = await gate.request(
            session_id="s", tool_call_id="tc-1", request=_approval_request()
        )
        assert decision.outcome is ApprovalOutcome.REJECT
        assert decision.decided_by == "auto:deny-all"
        assert decision.reason is not None


class TestMinimalContextManager:
    @pytest.mark.asyncio
    async def test_passes_history_through(self, companion_session: Session) -> None:
        ctx = MinimalContextManager(system_prompt="you are cairn")
        req = await ctx.build_request(
            session=companion_session, history=[], retrieved_memories=[], tools=[]
        )
        assert req.model == companion_session.model
        assert req.system == "you are cairn"
        assert req.messages == []
        assert req.tools == []

    @pytest.mark.asyncio
    async def test_empty_system_becomes_none(self, companion_session: Session) -> None:
        ctx = MinimalContextManager()
        req = await ctx.build_request(
            session=companion_session, history=[], retrieved_memories=[], tools=[]
        )
        assert req.system is None
