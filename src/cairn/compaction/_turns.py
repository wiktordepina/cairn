"""Turn-block detection for safe message truncation.

A "turn block" is a contiguous run of messages that starts at a user
message whose content is not purely tool results. Compacting by whole
turn blocks preserves two invariants that every provider we ship
assumes:

1. Every `ToolUseBlock` has a matching `ToolResultBlock`.
2. The first message after truncation has `role == "user"` with
   non-tool-result content (Anthropic requires this; OpenAI tolerates
   it but behaves better with it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cairn.domain import ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from cairn.domain import Message


@dataclass(frozen=True, slots=True)
class TurnBlock:
    """A contiguous range of messages forming a single logical turn."""

    start: int
    end: int  # exclusive
    message_count: int
    has_tool_pair: bool


def _is_pure_tool_result(message: Message) -> bool:
    """Return True if every content block in the message is a tool result."""
    if not message.content:
        return False
    return all(isinstance(block, ToolResultBlock) for block in message.content)


def _contains_tool_use(message: Message) -> bool:
    return any(isinstance(block, ToolUseBlock) for block in message.content)


def iter_turn_blocks(messages: Sequence[Message]) -> Iterator[TurnBlock]:
    """Yield `TurnBlock`s covering `messages` from left to right.

    A new block opens at every user message that is *not* pure tool
    results. All messages belong to exactly one block. If the first
    message is already an assistant reply or a pure-tool-result user
    message (unusual, but legal when replaying from a checkpoint), it
    is folded into the first block that opens after it, or — if no
    such block exists — into a single block spanning the whole
    sequence.
    """
    if not messages:
        return

    n = len(messages)

    # Locate block-start indices: user messages that are not pure
    # tool results.
    starts: list[int] = [
        i for i, msg in enumerate(messages) if msg.role == "user" and not _is_pure_tool_result(msg)
    ]

    if not starts:
        # Pathological sequence with no proper user turn: treat the
        # whole thing as one block.
        yield TurnBlock(
            start=0,
            end=n,
            message_count=n,
            has_tool_pair=any(_contains_tool_use(m) for m in messages),
        )
        return

    # Messages before the first block start attach to the first block
    # (shouldn't happen in practice — orchestrator always starts with
    # a user message — but we handle it so replays don't crash).
    first_start = 0 if starts[0] == 0 else 0
    starts[0] = first_start

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else n
        block_msgs = messages[start:end]
        yield TurnBlock(
            start=start,
            end=end,
            message_count=end - start,
            has_tool_pair=any(_contains_tool_use(m) for m in block_msgs),
        )
