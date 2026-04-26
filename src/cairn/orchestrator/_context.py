"""TurnContext — the execution context threaded through middleware and tools.

Passed to every middleware call and to `ToolRunner.run`. Carries the
information collaborators need without exposing the Orchestrator itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from cairn.domain import Session


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Read-only context for a turn's collaborators.

    The two `on_delegation_*` callbacks are populated by the
    orchestrator when constructing the context per iteration; the
    `DelegationTool` invokes them at the moment the child session
    row is created and the moment it's archived. Most tools ignore
    them — they're optional precisely so non-delegation tools don't
    have to care.
    """

    session: Session
    turn_id: str
    iteration: int
    on_delegation_spawned: Callable[[str], None] | None = field(default=None)
    on_delegation_completed: Callable[[str], None] | None = field(default=None)
