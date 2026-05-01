"""`recall` — Tier-0 read-only tool for searching session memory.

Symmetric with the `/recall` slash command: same `MemoryService`,
same scoring, same scope (the active session's `memory_space`). The
slash command is for the human; this tool gives the LLM the same
capability through a channel it can act on (a tool call whose result
becomes part of the next provider request).

Read-only and tier-0 — no approval gate. Parallels `now` and
`file_read`'s posture. The only side-effect surface a recall could
ever have is "user sees that the model peeked at memory", which is
already visible in the transcript.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from cairn.tools._decorator import tool

if TYPE_CHECKING:
    from collections.abc import Callable

    from cairn.memory._retrieval import MemoryService
    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._protocols import Tool


class RecallArgs(BaseModel):
    """Inputs for the `recall` tool.

    `k` is bounded 1..20 — narrower than the modal's 10 default
    because the LLM doesn't benefit from very long hit lists, and
    runaway `k=100` calls bloat the next request unnecessarily.
    """

    query: str = Field(
        ...,
        description="FTS5 query string. Words are AND-ed by the underlying search.",
    )
    k: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Maximum number of hits to return. Bounded 1..20.",
    )


def make_recall(
    memory_service: MemoryService,
    *,
    memory_space_provider: Callable[[TurnContext], str],
) -> Tool:
    """Build a `recall` tool bound to *memory_service*.

    Args:
        memory_service: The shared `MemoryService` instance used by
            the pre-turn retrieval preparer. Reusing it guarantees
            identical scoring between the auto-retrieve and the
            on-demand tool path.
        memory_space_provider: Callable that returns the memory space
            for the active turn. The active session's `memory_space`
            can change across `/clear` / `/archive`, so we read it
            fresh from `TurnContext` on each invocation rather than
            capturing a string at bootstrap.
    """

    @tool(
        name="recall",
        description=(
            "Search this session's persistent memory for entries matching "
            "the query. Returns top-k matches with content, type, and "
            "importance. Use when you need user context the conversation "
            "hasn't surfaced yet (preferences, prior decisions, named "
            "entities). Read-only; does not store anything. Skip when "
            "the relevant context is already visible in the current "
            "conversation."
        ),
        risk_tier=0,
        side_effects="none",
        timeout_s=5.0,
        args_model=RecallArgs,
    )
    async def recall(args: RecallArgs, ctx: TurnContext) -> str:
        space = memory_space_provider(ctx)
        if not space:
            return json.dumps({"hits": [], "note": "session has no memory_space"})
        hits = await memory_service.retrieve(
            space=space,
            query=args.query,
            k=args.k,
            truncate_content=False,
        )
        if not hits:
            return json.dumps({"hits": [], "note": "no matches"})
        return json.dumps(
            {
                "hits": [
                    {
                        "id": entry.id,
                        "content": entry.content,
                        "type": entry.entry_type.value,
                        "importance": entry.importance,
                        "created_at": entry.created_at.isoformat(),
                    }
                    for entry in hits
                ],
            }
        )

    return recall


__all__ = ["RecallArgs", "make_recall"]
