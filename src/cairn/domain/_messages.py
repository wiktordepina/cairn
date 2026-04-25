"""Message — the mutable unit of conversation during streaming.

A Message is built up incrementally as the provider streams tokens, then
persisted once complete. Individual content blocks are immutable; the
message's content list is append-only during construction.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from cairn.domain._content import (
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    content_list_adapter,
)

if TYPE_CHECKING:
    from cairn.domain._content import ContentBlock


class Message:
    """A single message in a conversation.

    Mutable during streaming (content list is built up), immutable once
    persisted to the database.
    """

    __slots__ = (
        "id",
        "session_id",
        "idx",
        "role",
        "content",
        "created_at",
        "_pending_tool_inputs",
    )

    def __init__(
        self,
        *,
        role: Literal["user", "assistant"],
        content: list[ContentBlock] | None = None,
        id: str | None = None,
        session_id: str = "",
        idx: int = 0,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id or uuid.uuid4().hex
        self.session_id = session_id
        self.idx = idx
        self.role = role
        self.content: list[ContentBlock] = content if content is not None else []
        self.created_at = created_at or datetime.now(UTC)
        self._pending_tool_inputs: dict[str, tuple[str, str]] = {}

    # -- Text streaming ----------------------------------------------------

    def append_text_delta(self, text: str) -> None:
        """Append text to the trailing TextBlock, or create a new one.

        Since TextBlock is frozen, the last element is replaced with a
        new TextBlock containing the accumulated text.
        """
        if self.content and isinstance(self.content[-1], TextBlock):
            existing = self.content[-1]
            self.content[-1] = TextBlock(text=existing.text + text)
        else:
            self.content.append(TextBlock(text=text))

    # -- Thinking streaming -----------------------------------------------

    def append_thinking_delta(self, text: str) -> None:
        """Append reasoning text to the leading ThinkingBlock, or create one.

        Reasoning content arrives before the final answer, so the
        ThinkingBlock sits at index 0 of the content list. ThinkingBlock
        is frozen, so we replace it on each delta.
        """
        if self.content and isinstance(self.content[0], ThinkingBlock):
            existing = self.content[0]
            self.content[0] = ThinkingBlock(thinking=existing.thinking + text)
        else:
            self.content.insert(0, ThinkingBlock(thinking=text))

    # -- Tool use streaming ------------------------------------------------

    def start_tool_use(self, tool_use_id: str, name: str) -> None:
        """Begin accumulating input for a tool call."""
        self._pending_tool_inputs[tool_use_id] = (name, "")

    def append_tool_input_delta(self, tool_use_id: str, delta: str) -> None:
        """Append a partial JSON fragment for a pending tool call."""
        if tool_use_id not in self._pending_tool_inputs:
            raise ValueError(f"No pending tool use with ID {tool_use_id!r}")
        name, accumulated = self._pending_tool_inputs[tool_use_id]
        self._pending_tool_inputs[tool_use_id] = (name, accumulated + delta)

    def finalize_tool_use(self, tool_use_id: str) -> None:
        """Parse accumulated JSON and append the finished ToolUseBlock."""
        if tool_use_id not in self._pending_tool_inputs:
            raise ValueError(f"No pending tool use with ID {tool_use_id!r}")
        name, raw_json = self._pending_tool_inputs.pop(tool_use_id)
        parsed: dict[str, object] = json.loads(raw_json) if raw_json else {}
        self.content.append(
            ToolUseBlock(id=tool_use_id, name=name, input=parsed)  # pyright: ignore[reportArgumentType]
        )

    # -- Accessors ---------------------------------------------------------

    def get_tool_use(self, tool_use_id: str) -> ToolUseBlock:
        """Find a ToolUseBlock by ID. Raises ValueError if not found."""
        for block in self.content:
            if isinstance(block, ToolUseBlock) and block.id == tool_use_id:
                return block
        raise ValueError(f"No ToolUseBlock with ID {tool_use_id!r}")

    def get_text(self) -> str:
        """Concatenate all TextBlock content into a single string."""
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    def has_pending_tool_input(self) -> bool:
        """True if any tool call started streaming but never finalized.

        Indicates that the model emitted ``ToolCallStart`` (and possibly
        ``ToolCallDelta`` chunks) but the stream ended before
        ``ToolCallEnd`` arrived — typically because the provider hit
        ``max_tokens`` mid-tool-call.
        """
        return bool(self._pending_tool_inputs)

    # -- Serialization -----------------------------------------------------

    def content_json(self) -> bytes:
        """Serialize content blocks to JSON bytes."""
        return content_list_adapter.dump_json(self.content)

    @classmethod
    def from_content_json(
        cls,
        *,
        id: str,
        session_id: str,
        idx: int,
        role: Literal["user", "assistant"],
        content_json: bytes | str,
        created_at: datetime,
    ) -> Message:
        """Reconstruct a Message from a database row."""
        raw = content_json if isinstance(content_json, bytes) else content_json.encode()
        content = content_list_adapter.validate_json(raw)
        return cls(
            id=id,
            session_id=session_id,
            idx=idx,
            role=role,
            content=content,
            created_at=created_at,
        )

    # -- Dunder ------------------------------------------------------------

    def __repr__(self) -> str:
        n_blocks = len(self.content)
        return f"Message(id={self.id!r}, role={self.role!r}, blocks={n_blocks}, idx={self.idx})"
