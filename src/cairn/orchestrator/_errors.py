"""Orchestrator-specific exceptions."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for orchestrator errors."""


class UnknownSession(OrchestratorError):
    """The referenced session does not exist or is archived."""


class TurnAlreadyRunning(OrchestratorError):
    """A turn is already in flight for this session.

    The orchestrator enforces one turn at a time per session. Fire
    ``Orchestrator.cancel(session_id)`` if you need to start a new one.
    """


class TurnBudgetExceeded(OrchestratorError):
    """A budget cap was hit during a turn.

    Surfaces via the ``TurnBlocked`` UI event for pre-turn caps, or via
    ``TurnAborted`` for mid-turn caps. This exception is raised internally
    when the orchestrator decides to halt; callers of ``run_turn`` see
    the corresponding UI events instead.
    """


class TurnTimeout(OrchestratorError):
    """The per-turn wall-clock limit was exceeded."""
