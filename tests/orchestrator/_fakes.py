"""Test helpers — fakes that implement orchestrator-facing protocols.

Kept in the test package (not shipped) because they exist to make
turn-loop tests deterministic without real providers / tools / UIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from cairn.domain._content import ToolResultBlock
from cairn.orchestrator._enums import ApprovalOutcome

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from cairn.domain import (
        MemoryEntry,
        ProviderEvent,
        ProviderRequest,
        Session,
        ToolDefinition,
        ToolUseBlock,
        UIEvent,
    )
    from cairn.orchestrator._context import TurnContext
    from cairn.orchestrator._middleware import ApprovalDecision
    from cairn.orchestrator._protocols import Tool


@dataclass
class FakeProvider:
    """A provider that replays canned event sequences.

    Constructed with a list of `list[ProviderEvent]`, one per call to
    `stream()`. Raises `StopIteration` (wrapped) if exhausted.

    Also tracks every request it received so tests can assert on what
    the orchestrator sent.
    """

    name: str = "fake"
    scripted: list[list[ProviderEvent]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    requests: list[ProviderRequest] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    _cursor: int = 0
    token_count: int = 42
    # Optional sleep before yielding each scripted batch — used by
    # tests that need wall-clock to elapse during the stream (turn
    # timeout watchdog, slow-stream behaviours).
    pre_yield_delay_s: float = 0.0

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        if self._cursor >= len(self.scripted):
            raise AssertionError(
                f"FakeProvider exhausted: no scripted response for call #{self._cursor + 1}"
            )
        events = self.scripted[self._cursor]
        self._cursor += 1
        return _gen(events, delay_s=self.pre_yield_delay_s)

    async def count_tokens(self, request: ProviderRequest) -> int:  # noqa: ARG002
        return self.token_count


async def _gen(
    events: list[ProviderEvent], *, delay_s: float = 0.0
) -> AsyncIterator[ProviderEvent]:
    if delay_s > 0:
        import asyncio  # noqa: PLC0415 — local to avoid widening the fake's import surface

        await asyncio.sleep(delay_s)
    for ev in events:
        yield ev


@dataclass
class EventCollector:
    """A `UIEventObserver` that records every event.

    Tests assert on the full sequence.
    """

    events: list[UIEvent] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def observe(self, event: UIEvent) -> None:
        self.events.append(event)

    def by_type(self, cls: type) -> list[UIEvent]:
        return [e for e in self.events if isinstance(e, cls)]


@dataclass
class RecordingToolRunner:
    """A `ToolRunner` that dispatches to a dict of name → async callable.

    Each callable takes `(tool_call, session, turn_id)` and returns
    `(output_dict, is_error)`. The runner wraps the output in a
    `ToolResultBlock` and returns it.
    """

    handlers: dict[str, Callable[..., Awaitable[tuple[str, bool]]]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    async def run(
        self,
        *,
        tool_call: ToolUseBlock,
        tool: Tool,  # noqa: ARG002
        session: Session,
        turn_id: str,
        ctx: TurnContext,  # noqa: ARG002
        decision: ApprovalDecision,
        message_id: str,
    ) -> ToolResultBlock:
        self.calls.append(
            {
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "args": dict(tool_call.input),
                "session_id": session.id,
                "turn_id": turn_id,
                "decision": decision,
                "message_id": message_id,
            }
        )
        # Mirror the real runner: on REJECT the handler is never invoked —
        # the audit row is recorded and an error block is synthesised.
        if decision.outcome is ApprovalOutcome.REJECT:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=decision.reason or "Tool rejected by approver.",
                is_error=True,
            )
        handler = self.handlers.get(tool_call.name)
        if handler is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content="(no handler registered)",
                is_error=True,
            )
        content, is_error = await handler(tool_call, session, turn_id)
        return ToolResultBlock(
            tool_use_id=tool_call.id,
            content=content,
            is_error=is_error,
        )


@dataclass
class StubTool:
    """A minimal `Tool` that satisfies the orchestrator's protocol.

    Execution is delegated to `RecordingToolRunner` — this stub exists
    so `ToolRegistry.get(name)` has something to return.
    """

    name: str
    description: str = "stub tool for tests"
    input_schema: dict[str, object] = field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    tool_kind: Literal["native", "mcp", "delegation"] = "native"
    approval_required: bool = False
    risk_tier: int = 0
    side_effects: Literal["none", "read", "write"] = "none"
    timeout_s: float = 30.0

    async def invoke(
        self,
        args: dict[str, object],  # noqa: ARG002
        ctx: TurnContext,  # noqa: ARG002
    ) -> ToolResultBlock:  # pragma: no cover
        raise NotImplementedError("Use RecordingToolRunner to dispatch StubTool")


@dataclass
class DictToolRegistry:
    """A `ToolRegistry` backed by a name → Tool dict."""

    tools: dict[str, Tool] = field(default_factory=dict)
    definitions: list[ToolDefinition] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def for_session(self, session: Session) -> list[ToolDefinition]:  # noqa: ARG002
        return list(self.definitions)

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)


@dataclass
class SpyMemoryService:
    """Memory service that records its `retrieve` calls."""

    canned: list[MemoryEntry] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    calls: list[dict[str, Any]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    async def retrieve(self, *, space: str, query: str, k: int) -> list[MemoryEntry]:
        self.calls.append({"space": space, "query": query, "k": k})
        return list(self.canned)


@dataclass
class SpyExtractionQueue:
    """Extraction queue that records every submission."""

    submissions: list[dict[str, Any]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def submit(self, *, session_id: str, since_idx: int, turn_id: str) -> None:
        self.submissions.append(
            {"session_id": session_id, "since_idx": since_idx, "turn_id": turn_id}
        )
