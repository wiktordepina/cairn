"""Collaborator protocols — everything the orchestrator depends on.

Each collaborator is injected at construction. The orchestrator only
talks to protocols, never to concrete implementations. The tool system,
memory brick, and UI layer plug in through these protocols.

Stubs for every protocol live in ``_stubs.py`` so the orchestrator can
be constructed (and tested) before its downstream bricks exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cairn.domain import (
        MemoryEntry,
        Message,
        ProviderRequest,
        Session,
        ToolDefinition,
        ToolResultBlock,
        ToolUseBlock,
        UsageEvent,
    )
    from cairn.domain._enums import UsageOperation
    from cairn.orchestrator._context import TurnContext
    from cairn.orchestrator._enums import BudgetVerdict
    from cairn.orchestrator._middleware import ApprovalDecision, ApprovalRequest


@runtime_checkable
class Tool(Protocol):
    """Minimum shape the orchestrator knows about a tool.

    The tool-system brick provides concrete ``Tool`` classes with richer
    surface (invocation, schemas, MCP server bindings); the orchestrator
    only needs metadata plus a way to invoke.
    """

    @property
    def name(self) -> str: ...

    @property
    def approval_required(self) -> bool: ...

    @property
    def risk_tier(self) -> int: ...                                # 0..5

    @property
    def side_effects(self) -> Literal["none", "read", "write"]: ...

    @property
    def timeout_s(self) -> float: ...

    async def invoke(
        self,
        args: dict[str, object],
        ctx: TurnContext,
    ) -> ToolResultBlock: ...


@runtime_checkable
class ToolRegistry(Protocol):
    """Owns discovery + session-scoping of tools."""

    def for_session(self, session: Session) -> list[ToolDefinition]:
        """Return tool *definitions* the session may see.

        Scoping rules (enforced by the concrete registry, not the orchestrator):
        - ``EPHEMERAL`` → ``[]`` by default
        - ``PERSONA`` → per-persona allowlist
        - ``COMPANION`` → full set
        """
        ...

    def get(self, name: str) -> Tool | None:
        """Return the concrete ``Tool`` implementation by name, or None."""
        ...


@runtime_checkable
class ToolRunner(Protocol):
    """Owns the execution of a single tool call.

    Drives the per-call state machine (pending → approved → executing →
    terminal), records transitions via ``ToolCallRepo``, and applies the
    ``ResultTransformer`` chain before returning.
    """

    async def run(
        self,
        *,
        tool_call: ToolUseBlock,
        tool: Tool,
        session: Session,
        turn_id: str,
        ctx: TurnContext,
    ) -> ToolResultBlock: ...


@runtime_checkable
class MemoryService(Protocol):
    """Read side of the memory layer."""

    async def retrieve(
        self,
        *,
        space: str,
        query: str,
        k: int,
    ) -> list[MemoryEntry]: ...


@runtime_checkable
class ExtractionQueue(Protocol):
    """Fire-and-forget queue for post-turn observation extraction.

    ``submit`` MUST NOT block the turn loop. Back-pressure, if any, is
    handled inside the extractor's own worker — submissions always return
    immediately.
    """

    def submit(
        self,
        *,
        session_id: str,
        since_idx: int,
        turn_id: str,
    ) -> None: ...


@runtime_checkable
class ApprovalGateway(Protocol):
    """Terminal approver at the end of the ``ToolApprover`` chain.

    Typically posts an approval-pending UI event and awaits a user
    response. The in-module stubs ``AutoApproveGateway`` and
    ``DenyAllGateway`` bypass UI interaction.
    """

    async def request(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        request: ApprovalRequest,
    ) -> ApprovalDecision: ...


@runtime_checkable
class ContextManager(Protocol):
    """Assembles the provider request from all the moving parts."""

    async def build_request(
        self,
        *,
        session: Session,
        history: list[Message],
        retrieved_memories: list[MemoryEntry],
        tools: list[ToolDefinition],
    ) -> ProviderRequest: ...


@runtime_checkable
class CostTracker(Protocol):
    """Cost recording and budget enforcement."""

    async def record(
        self,
        *,
        session_id: str,
        parent_session_id: str | None,
        turn_id: str,
        message_id: str | None,
        usage: UsageEvent,
        provider: str,
        model: str,
        role: str,
        operation: UsageOperation,
        duration_ms: int | None,
        cost_usd: float,
    ) -> None: ...

    async def should_block_turn(
        self,
        *,
        session_id: str,
    ) -> BudgetVerdict: ...
