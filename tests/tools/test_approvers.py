"""Tests for approver middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.orchestrator import ApprovalOutcome, ApprovalRequest
from cairn.tools import AutoApproveReadOnly, SessionAllowlist, TierGate

if TYPE_CHECKING:
    from cairn.orchestrator._context import TurnContext


def _req(
    *,
    name: str = "file_read",
    risk_tier: int = 1,
    side_effects: str = "read",
    args: dict | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        tool_call_id="tc-1",
        tool_name=name,
        args=args or {},
        risk_tier=risk_tier,
        side_effects=side_effects,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# AutoApproveReadOnly
# ---------------------------------------------------------------------------


class TestAutoApproveReadOnly:
    @pytest.mark.asyncio
    async def test_approves_tier_0_none(self, turn_ctx: TurnContext) -> None:
        a = AutoApproveReadOnly()
        d = await a.decide(
            _req(name="calc", risk_tier=0, side_effects="none"), turn_ctx
        )
        assert d.outcome is ApprovalOutcome.APPROVE

    @pytest.mark.asyncio
    async def test_approves_tier_1_read(self, turn_ctx: TurnContext) -> None:
        a = AutoApproveReadOnly()
        d = await a.decide(_req(risk_tier=1, side_effects="read"), turn_ctx)
        assert d.outcome is ApprovalOutcome.APPROVE

    @pytest.mark.asyncio
    async def test_approves_tier_2_read(self, turn_ctx: TurnContext) -> None:
        a = AutoApproveReadOnly()
        d = await a.decide(_req(risk_tier=2, side_effects="read"), turn_ctx)
        assert d.outcome is ApprovalOutcome.APPROVE

    @pytest.mark.asyncio
    async def test_escalates_tier_3_write(self, turn_ctx: TurnContext) -> None:
        a = AutoApproveReadOnly()
        d = await a.decide(
            _req(name="file_write", risk_tier=3, side_effects="write"), turn_ctx
        )
        assert d.outcome is ApprovalOutcome.ESCALATE

    @pytest.mark.asyncio
    async def test_escalates_tier_4(self, turn_ctx: TurnContext) -> None:
        a = AutoApproveReadOnly()
        d = await a.decide(
            _req(name="send_email", risk_tier=4, side_effects="write"), turn_ctx
        )
        assert d.outcome is ApprovalOutcome.ESCALATE


# ---------------------------------------------------------------------------
# SessionAllowlist
# ---------------------------------------------------------------------------


class TestSessionAllowlist:
    @pytest.mark.asyncio
    async def test_escalates_when_empty(self, turn_ctx: TurnContext) -> None:
        a = SessionAllowlist()
        d = await a.decide(_req(), turn_ctx)
        assert d.outcome is ApprovalOutcome.ESCALATE

    @pytest.mark.asyncio
    async def test_approves_after_remember(self, turn_ctx: TurnContext) -> None:
        a = SessionAllowlist()
        req = _req(name="file_write", args={"path": "foo.py"})
        a.remember_user_approval(session_id=turn_ctx.session.id, request=req)
        d = await a.decide(req, turn_ctx)
        assert d.outcome is ApprovalOutcome.APPROVE
        assert d.decided_by == "auto:session-allowlist"

    @pytest.mark.asyncio
    async def test_different_args_require_reapproval(
        self, turn_ctx: TurnContext
    ) -> None:
        a = SessionAllowlist()
        req_foo = _req(name="file_write", args={"path": "foo.py"})
        req_bar = _req(name="file_write", args={"path": "bar.py"})
        a.remember_user_approval(session_id=turn_ctx.session.id, request=req_foo)
        d_foo = await a.decide(req_foo, turn_ctx)
        d_bar = await a.decide(req_bar, turn_ctx)
        assert d_foo.outcome is ApprovalOutcome.APPROVE
        assert d_bar.outcome is ApprovalOutcome.ESCALATE

    @pytest.mark.asyncio
    async def test_different_tool_requires_reapproval(
        self, turn_ctx: TurnContext
    ) -> None:
        a = SessionAllowlist()
        a.remember_user_approval(
            session_id=turn_ctx.session.id,
            request=_req(name="file_write", args={"path": "x"}),
        )
        d = await a.decide(
            _req(name="file_read", args={"path": "x"}), turn_ctx
        )
        assert d.outcome is ApprovalOutcome.ESCALATE

    @pytest.mark.asyncio
    async def test_session_isolation(self, turn_ctx: TurnContext) -> None:
        a = SessionAllowlist()
        other_session = "sess-other"
        req = _req(args={"p": 1})
        # Remember for a different session.
        a.remember_user_approval(session_id=other_session, request=req)
        # Current session still needs approval.
        d = await a.decide(req, turn_ctx)
        assert d.outcome is ApprovalOutcome.ESCALATE

    def test_forget_session(self, turn_ctx: TurnContext) -> None:
        a = SessionAllowlist()
        a.remember_user_approval(
            session_id=turn_ctx.session.id, request=_req(args={"a": 1})
        )
        a.forget_session(turn_ctx.session.id)
        # Internal state dropped — no way to assert other than re-decide.
        # We test via decide semantics in the next test.

    @pytest.mark.asyncio
    async def test_forget_clears_approvals(self, turn_ctx: TurnContext) -> None:
        a = SessionAllowlist()
        req = _req(args={"a": 1})
        a.remember_user_approval(session_id=turn_ctx.session.id, request=req)
        a.forget_session(turn_ctx.session.id)
        d = await a.decide(req, turn_ctx)
        assert d.outcome is ApprovalOutcome.ESCALATE


# ---------------------------------------------------------------------------
# TierGate
# ---------------------------------------------------------------------------


class TestTierGate:
    @pytest.mark.asyncio
    async def test_escalates_tier_4(self, turn_ctx: TurnContext) -> None:
        g = TierGate()
        d = await g.decide(_req(risk_tier=4, side_effects="write"), turn_ctx)
        assert d.outcome is ApprovalOutcome.ESCALATE
        assert d.decided_by == "tier-gate"

    @pytest.mark.asyncio
    async def test_escalates_tier_lower(self, turn_ctx: TurnContext) -> None:
        # TierGate always escalates — earlier approvers have had their say,
        # and for Tier 3 we also want the gateway to see it (first-run).
        g = TierGate()
        d = await g.decide(_req(risk_tier=3, side_effects="write"), turn_ctx)
        assert d.outcome is ApprovalOutcome.ESCALATE
