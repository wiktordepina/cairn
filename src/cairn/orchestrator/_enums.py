"""Orchestrator enums — state machine states, budget verdicts, approval outcomes."""

from __future__ import annotations

from enum import StrEnum


class TurnState(StrEnum):
    """States of the orchestrator's per-turn state machine.

    The ``turns.state`` column is authoritative — every transition writes
    here via a conditional UPDATE. A turn ends in exactly one terminal
    state: ``completed`` or ``aborted``.
    """

    STARTED = "started"
    MEMORY_RETRIEVAL = "memory_retrieval"
    ITERATION = "iteration"  # in the tool loop
    CONTEXT_ASSEMBLY = "context_assembly"
    PROVIDER_STREAMING = "provider_streaming"
    TOOL_DISPATCH = "tool_dispatch"
    FINALISING = "finalising"
    EXTRACTION_ENQUEUED = "extraction_enqueued"
    COMPLETED = "completed"
    ABORTED = "aborted"


TERMINAL_STATES: frozenset[TurnState] = frozenset({TurnState.COMPLETED, TurnState.ABORTED})


class BudgetVerdict(StrEnum):
    """Result of a pre-turn budget check."""

    PROCEED = "proceed"
    WARN = "warn"
    BLOCK = "block"


class ApprovalOutcome(StrEnum):
    """Result returned by a ``ToolApprover``.

    ``ESCALATE`` means "I can't decide — pass to the next approver in the
    chain, or the gateway". The first non-escalate decision wins.
    """

    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
