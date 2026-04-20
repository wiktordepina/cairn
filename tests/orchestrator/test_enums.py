"""Tests for orchestrator enums."""

from __future__ import annotations

from cairn.orchestrator import ApprovalOutcome, BudgetVerdict, TurnState
from cairn.orchestrator._enums import TERMINAL_STATES


class TestTurnState:
    def test_terminal_states(self) -> None:
        assert {TurnState.COMPLETED, TurnState.ABORTED} == TERMINAL_STATES

    def test_string_values(self) -> None:
        assert TurnState.STARTED == "started"
        assert TurnState.COMPLETED == "completed"
        assert TurnState.ABORTED == "aborted"

    def test_non_terminal_states(self) -> None:
        non_terminal = set(TurnState) - TERMINAL_STATES
        assert TurnState.STARTED in non_terminal
        assert TurnState.PROVIDER_STREAMING in non_terminal


class TestBudgetVerdict:
    def test_values(self) -> None:
        assert BudgetVerdict.PROCEED == "proceed"
        assert BudgetVerdict.WARN == "warn"
        assert BudgetVerdict.BLOCK == "block"


class TestApprovalOutcome:
    def test_values(self) -> None:
        assert ApprovalOutcome.APPROVE == "approve"
        assert ApprovalOutcome.REJECT == "reject"
        assert ApprovalOutcome.ESCALATE == "escalate"
