"""Content block types — the vocabulary of message content.

Each block is a frozen Pydantic model with a `type` literal discriminator.
The `ContentBlock` union and `content_list_adapter` provide serialization
for the `content_json` database column.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TextBlock(BaseModel):
    """Plain text content."""

    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """A tool call with parsed input arguments."""

    model_config = ConfigDict(frozen=True)

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ImageSource(BaseModel):
    """Image data source (base64-encoded)."""

    model_config = ConfigDict(frozen=True)

    media_type: str
    data: str


class ImageBlock(BaseModel):
    """Image content for vision-capable models."""

    model_config = ConfigDict(frozen=True)

    type: Literal["image"] = "image"
    source: ImageSource


class ThinkingBlock(BaseModel):
    """Extended reasoning / chain-of-thought content."""

    model_config = ConfigDict(frozen=True)

    type: Literal["thinking"] = "thinking"
    thinking: str


class ToolResultBlock(BaseModel):
    """Result returned from a tool call.

    `content` can be a plain string or a list of content blocks
    (for rich tool results containing images, etc.).
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: (
        str
        | list[
            Annotated[
                TextBlock | ToolUseBlock | ImageBlock | ThinkingBlock,
                Field(discriminator="type"),
            ]
        ]
    ) = ""
    is_error: bool = False


# ---------------------------------------------------------------------------
# Discriminated union and TypeAdapter for JSON serialization
# ---------------------------------------------------------------------------

ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock | ImageBlock | ThinkingBlock,
    Field(discriminator="type"),
]

content_list_adapter: TypeAdapter[list[ContentBlock]] = TypeAdapter(list[ContentBlock])
"""Adapter for serializing/deserializing lists of content blocks.

Usage::

    json_bytes = content_list_adapter.dump_json(blocks)
    blocks = content_list_adapter.validate_json(json_bytes)
"""
