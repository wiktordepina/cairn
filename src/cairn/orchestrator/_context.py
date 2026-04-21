"""TurnContext — the execution context threaded through middleware and tools.

Passed to every middleware call and to `ToolRunner.run`. Carries the
information collaborators need without exposing the Orchestrator itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.domain import Session


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Read-only context for a turn's collaborators."""

    session: Session
    turn_id: str
    iteration: int
