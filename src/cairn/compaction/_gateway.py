"""Budget-overflow gateway — resolves preserve-floor advisories.

When compaction drops everything it is allowed to drop and the
request is still over the advisory budget, the preparer consults a
`BudgetOverflowGateway`. The gateway either prompts the user (via the
UI brick) or replays a previously-remembered decision.

Two default stubs ship here: `AutoContinueOverflowGateway` (useful for
tests and "I know what I'm doing" headless runs) and
`AutoTerminateOverflowGateway` (the orchestrator default — fail
loudly rather than silently burn tokens).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cairn.domain import BudgetOverflowAdvisory
    from cairn.orchestrator._context import TurnContext


OverflowDecision = Literal["continue", "terminate"]


@runtime_checkable
class BudgetOverflowGateway(Protocol):
    """Resolves a preserve-floor overflow into a user decision."""

    async def decide(
        self,
        advisory: BudgetOverflowAdvisory,
        ctx: TurnContext,
    ) -> OverflowDecision: ...


class AutoContinueOverflowGateway:
    """Always returns ``'continue'``. Useful for tests and non-interactive runs
    where the operator has explicitly accepted the overflow risk."""

    async def decide(
        self,
        advisory: BudgetOverflowAdvisory,  # noqa: ARG002
        ctx: TurnContext,  # noqa: ARG002
    ) -> OverflowDecision:
        return "continue"


class AutoTerminateOverflowGateway:
    """Always returns ``'terminate'``. The orchestrator default — fails
    loudly rather than silently overflowing the provider."""

    async def decide(
        self,
        advisory: BudgetOverflowAdvisory,  # noqa: ARG002
        ctx: TurnContext,  # noqa: ARG002
    ) -> OverflowDecision:
        return "terminate"
