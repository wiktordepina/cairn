"""TurnContext — the execution context threaded through middleware and tools.

Passed to every middleware call and to ``ToolRunner.run``. Carries the
information collaborators need without exposing the Orchestrator itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from cairn.domain import Session, UIEvent


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Read-only context for a turn's collaborators.

    ``emit`` is how tools surface high-level UI events during their own
    execution (notably ``DelegationSpawned`` / ``DelegationCompleted``
    from inside ``DelegationTool.invoke``). It is not a generic back
    door — the orchestrator owns most of the event emission.
    """

    session: Session
    turn_id: str
    iteration: int
    emit: Callable[[UIEvent], None]
