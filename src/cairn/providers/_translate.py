"""Shared translation helpers for provider adapters.

These helpers walk cairn Message content blocks and provide utilities
used by multiple adapters. Provider-specific formatting lives in each
adapter module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.domain._content import (
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from cairn.domain._enums import StopReason

if TYPE_CHECKING:
    from cairn.domain._messages import Message


def extract_text(message: Message) -> str:
    """Concatenate all TextBlock content from a message."""
    return "".join(b.text for b in message.content if isinstance(b, TextBlock))


def has_tool_uses(message: Message) -> bool:
    """Check if a message contains any ToolUseBlock."""
    return any(isinstance(b, ToolUseBlock) for b in message.content)


def has_tool_results(message: Message) -> bool:
    """Check if a message contains any ToolResultBlock."""
    return any(isinstance(b, ToolResultBlock) for b in message.content)


def get_tool_uses(message: Message) -> list[ToolUseBlock]:
    """Extract all ToolUseBlock instances from a message."""
    return [b for b in message.content if isinstance(b, ToolUseBlock)]


def get_tool_results(message: Message) -> list[ToolResultBlock]:
    """Extract all ToolResultBlock instances from a message."""
    return [b for b in message.content if isinstance(b, ToolResultBlock)]


def get_images(message: Message) -> list[ImageBlock]:
    """Extract all ImageBlock instances from a message."""
    return [b for b in message.content if isinstance(b, ImageBlock)]


def get_thinking(message: Message) -> list[ThinkingBlock]:
    """Extract all ThinkingBlock instances from a message."""
    return [b for b in message.content if isinstance(b, ThinkingBlock)]


# ---------------------------------------------------------------------------
# Stop reason mapping
# ---------------------------------------------------------------------------

_ANTHROPIC_STOP_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
    "tool_use": StopReason.TOOL_USE,
}

_OPENAI_STOP_MAP: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "tool_calls": StopReason.TOOL_USE,
}


def map_anthropic_stop_reason(reason: str | None) -> StopReason:
    """Map an Anthropic stop reason string to a domain StopReason."""
    if reason is None:
        return StopReason.END_TURN
    return _ANTHROPIC_STOP_MAP.get(reason, StopReason.END_TURN)


def map_openai_stop_reason(reason: str | None) -> StopReason:
    """Map an OpenAI finish_reason string to a domain StopReason."""
    if reason is None:
        return StopReason.END_TURN
    return _OPENAI_STOP_MAP.get(reason, StopReason.END_TURN)
