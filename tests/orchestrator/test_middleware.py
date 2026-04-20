"""Tests for middleware data shapes."""

from __future__ import annotations

import dataclasses

import pytest

from cairn.orchestrator import ApprovalDecision, ApprovalOutcome, ApprovalRequest


class TestApprovalRequest:
    def test_construction(self) -> None:
        req = ApprovalRequest(
            tool_call_id="tc-1",
            tool_name="shell.run",
            args={"cmd": "ls"},
            risk_tier=3,
            side_effects="write",
        )
        assert req.tool_name == "shell.run"
        assert req.side_effects == "write"

    def test_frozen(self) -> None:
        req = ApprovalRequest(
            tool_call_id="tc-1", tool_name="x", args={}, risk_tier=0, side_effects="none"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.tool_name = "y"  # type: ignore[misc]


class TestApprovalDecision:
    def test_approve(self) -> None:
        d = ApprovalDecision(outcome=ApprovalOutcome.APPROVE, decided_by="user")
        assert d.outcome is ApprovalOutcome.APPROVE
        assert d.reason is None

    def test_reject_with_reason(self) -> None:
        d = ApprovalDecision(
            outcome=ApprovalOutcome.REJECT, decided_by="user", reason="not trusted"
        )
        assert d.reason == "not trusted"

    def test_escalate(self) -> None:
        d = ApprovalDecision(outcome=ApprovalOutcome.ESCALATE, decided_by="auto:read-only")
        assert d.outcome is ApprovalOutcome.ESCALATE
