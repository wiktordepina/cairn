"""Errors raised by the compaction pipeline."""

from __future__ import annotations


class BudgetOverflowDeclined(Exception):
    """Raised by `TruncatingCompactor` when the overflow gateway
    resolved the advisory as ``terminate``. The orchestrator catches
    this in the turn loop, emits `TurnAborted` with
    ``reason='user_declined_overflow'``, and archives the session.
    """

    def __init__(self, session_id: str, turn_id: str) -> None:
        super().__init__(
            f"User declined context-overflow advisory on session {session_id!r} turn {turn_id!r}"
        )
        self.session_id = session_id
        self.turn_id = turn_id
