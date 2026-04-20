"""Shared fixtures and factory helpers for domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cairn.domain._content import TextBlock, ToolResultBlock, ToolUseBlock
from cairn.domain._messages import Message
from cairn.domain._sessions import Session


def make_message(
    *,
    role: str = "assistant",
    text: str | None = None,
    **kwargs: Any,
) -> Message:
    """Create a Message with sensible defaults."""
    msg = Message(role=role, **kwargs)  # type: ignore[arg-type]
    if text is not None:
        msg.content.append(TextBlock(text=text))
    return msg


def make_session(**overrides: Any) -> Session:
    """Create a Session with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": "sess-001",
        "type": "companion",
        "persona": "companion",
        "model": "claude-opus-4-7",
        "memory_space": "companion",
        "created_at": datetime(2026, 4, 19, tzinfo=UTC),
        "updated_at": datetime(2026, 4, 19, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Session(**defaults)


def make_tool_use(
    *,
    id: str = "tc-001",
    name: str = "web_fetch",
    input: dict[str, Any] | None = None,
) -> ToolUseBlock:
    """Create a ToolUseBlock with sensible defaults."""
    return ToolUseBlock(id=id, name=name, input=input or {"url": "https://example.com"})


def make_tool_result(
    *,
    tool_use_id: str = "tc-001",
    content: str = "Result text",
    is_error: bool = False,
) -> ToolResultBlock:
    """Create a ToolResultBlock with sensible defaults."""
    return ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)
