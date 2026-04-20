"""Default stubs for orchestrator collaborators.

These let the orchestrator be constructed and tested before its downstream
bricks (tool system, memory, UI) exist. Each stub is a correct-but-minimal
implementation of its protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.domain._provider import ProviderRequest
from cairn.orchestrator._enums import ApprovalOutcome
from cairn.orchestrator._middleware import ApprovalDecision, ApprovalRequest

if TYPE_CHECKING:
    from cairn.domain import (
        MemoryEntry,
        Message,
        Session,
        ToolDefinition,
    )
    from cairn.orchestrator._protocols import Tool


class NullMemoryService:
    """Returns no memories. Default until the memory brick lands."""

    async def retrieve(
        self, *, space: str, query: str, k: int  # noqa: ARG002
    ) -> list[MemoryEntry]:
        return []


class NullExtractionQueue:
    """No-op submission. Default until the memory brick lands."""

    def submit(
        self, *, session_id: str, since_idx: int, turn_id: str  # noqa: ARG002
    ) -> None:
        return None


class EmptyToolRegistry:
    """No tools, ever. Default until the tool-system brick lands."""

    def for_session(self, session: Session) -> list[ToolDefinition]:  # noqa: ARG002
        return []

    def get(self, name: str) -> Tool | None:  # noqa: ARG002
        return None


class RaisingToolRunner:
    """Rejects every dispatch. Paired with ``EmptyToolRegistry`` so it can
    never be called in practice; raises loudly if wiring goes wrong."""

    async def run(self, **kwargs: object) -> object:  # pragma: no cover
        raise NotImplementedError(
            "ToolRunner not configured — the tool system brick has not landed. "
            "If you see this, the orchestrator dispatched a tool call despite "
            "EmptyToolRegistry claiming no tools exist."
        )


class AutoApproveGateway:
    """Approves every request. Safe as long as no real tools are registered."""

    async def request(
        self, *, session_id: str, tool_call_id: str, request: ApprovalRequest  # noqa: ARG002
    ) -> ApprovalDecision:
        return ApprovalDecision(
            outcome=ApprovalOutcome.APPROVE,
            decided_by="auto:stub",
            reason=None,
        )


class DenyAllGateway:
    """Rejects every request. Safe default for contexts that shouldn't
    execute tools (e.g. ephemeral sessions)."""

    async def request(
        self, *, session_id: str, tool_call_id: str, request: ApprovalRequest  # noqa: ARG002
    ) -> ApprovalDecision:
        return ApprovalDecision(
            outcome=ApprovalOutcome.REJECT,
            decided_by="auto:deny-all",
            reason="Tool execution is disabled for this session.",
        )


class MinimalContextManager:
    """Bare-bones context assembly: passes history through, no compaction,
    no memory injection, no convention files. Real ``ContextManager``
    implementations land with their respective bricks."""

    def __init__(self, *, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt

    async def build_request(
        self,
        *,
        session: Session,
        history: list[Message],
        retrieved_memories: list[MemoryEntry],  # noqa: ARG002
        tools: list[ToolDefinition],
    ) -> ProviderRequest:
        return ProviderRequest(
            model=session.model,
            messages=list(history),
            system=self._system_prompt or None,
            tools=tools,
        )
