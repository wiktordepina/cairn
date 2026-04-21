"""Middleware protocols — typed extensibility seams for the turn loop.

Four middleware types, each with a narrow contract:

- `MessagePreparer` — rewrites the `ProviderRequest` before the model sees it
  (memory injection, compaction, cache markers).
- `ToolApprover` — gates tool execution, returns APPROVE | REJECT | ESCALATE.
- `ResultTransformer` — scrubs tool results before they hit the session
  (spotlighting, Unicode stripping, secret redaction).
- `UIEventObserver` — sync fan-out of UI events for logging, metrics,
  cost dashboards. Can't mutate, can't back-pressure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cairn.domain import ProviderRequest, ToolResultBlock, ToolUseBlock, UIEvent
    from cairn.orchestrator._context import TurnContext

from cairn.orchestrator._enums import ApprovalOutcome  # noqa: TCH001


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Payload handed to a `ToolApprover` / `ApprovalGateway`."""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    risk_tier: int  # 0..5
    side_effects: Literal["none", "read", "write"]


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Outcome returned by a `ToolApprover` / `ApprovalGateway`."""

    outcome: ApprovalOutcome
    decided_by: str  # 'user', 'auto:read-only', etc.
    reason: str | None = None


@runtime_checkable
class MessagePreparer(Protocol):
    """Rewrites the provider request before the model sees it."""

    async def prepare(
        self,
        request: ProviderRequest,
        ctx: TurnContext,
    ) -> ProviderRequest: ...


@runtime_checkable
class ToolApprover(Protocol):
    """Decides whether a tool call may proceed.

    Return `ESCALATE` to defer to the next approver in the chain, or
    to the `ApprovalGateway` at the end of the chain.
    """

    async def decide(
        self,
        request: ApprovalRequest,
        ctx: TurnContext,
    ) -> ApprovalDecision: ...


@runtime_checkable
class ResultTransformer(Protocol):
    """Rewrites a tool result before it is persisted + sent back to the model."""

    async def transform(
        self,
        result: ToolResultBlock,
        tool_call: ToolUseBlock,
        ctx: TurnContext,
    ) -> ToolResultBlock: ...


@runtime_checkable
class UIEventObserver(Protocol):
    """Sync, side-effecting observer of UI events. Fire-and-forget.

    If an observer raises, the orchestrator catches at ERROR and continues.
    Observers cannot mutate events and cannot back-pressure the turn loop.
    """

    def observe(self, event: UIEvent) -> None: ...
