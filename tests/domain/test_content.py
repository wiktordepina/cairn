"""Tests for content block types and JSON serialization."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cairn.domain._content import (
    ContentBlock,
    ImageBlock,
    ImageSource,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    content_list_adapter,
)


class TestTextBlock:
    def test_construction(self) -> None:
        block = TextBlock(text="hello")
        assert block.text == "hello"
        assert block.type == "text"

    def test_frozen(self) -> None:
        block = TextBlock(text="hello")
        with pytest.raises(ValidationError):
            block.text = "world"  # type: ignore[misc]


class TestToolUseBlock:
    def test_construction(self) -> None:
        block = ToolUseBlock(id="tc-1", name="fetch", input={"url": "https://x.com"})
        assert block.id == "tc-1"
        assert block.name == "fetch"
        assert block.input == {"url": "https://x.com"}
        assert block.type == "tool_use"


class TestToolResultBlock:
    def test_string_content(self) -> None:
        block = ToolResultBlock(tool_use_id="tc-1", content="result")
        assert block.content == "result"
        assert block.is_error is False

    def test_error_flag(self) -> None:
        block = ToolResultBlock(tool_use_id="tc-1", content="oops", is_error=True)
        assert block.is_error is True

    def test_nested_content_blocks(self) -> None:
        block = ToolResultBlock(
            tool_use_id="tc-1",
            content=[TextBlock(text="nested text")],
        )
        assert isinstance(block.content, list)
        assert len(block.content) == 1

    def test_default_content_empty_string(self) -> None:
        block = ToolResultBlock(tool_use_id="tc-1")
        assert block.content == ""


class TestImageBlock:
    def test_construction(self) -> None:
        source = ImageSource(media_type="image/png", data="iVBOR...")
        block = ImageBlock(source=source)
        assert block.type == "image"
        assert block.source.media_type == "image/png"


class TestThinkingBlock:
    def test_construction(self) -> None:
        block = ThinkingBlock(thinking="Let me think about this...")
        assert block.type == "thinking"


class TestContentListAdapter:
    def test_round_trip_text(self) -> None:
        blocks: list[ContentBlock] = [TextBlock(text="hello")]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert len(restored) == 1
        assert isinstance(restored[0], TextBlock)
        assert restored[0].text == "hello"

    def test_round_trip_tool_use(self) -> None:
        blocks: list[ContentBlock] = [ToolUseBlock(id="tc-1", name="fetch", input={"url": "x"})]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert isinstance(restored[0], ToolUseBlock)
        assert restored[0].name == "fetch"

    def test_round_trip_mixed(self) -> None:
        blocks: list[ContentBlock] = [
            TextBlock(text="before"),
            ToolUseBlock(id="tc-1", name="calc", input={"x": 1}),
            TextBlock(text="after"),
        ]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert len(restored) == 3
        assert isinstance(restored[0], TextBlock)
        assert isinstance(restored[1], ToolUseBlock)
        assert isinstance(restored[2], TextBlock)

    def test_round_trip_tool_result(self) -> None:
        blocks: list[ContentBlock] = [ToolResultBlock(tool_use_id="tc-1", content="done")]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert isinstance(restored[0], ToolResultBlock)
        assert restored[0].content == "done"

    def test_round_trip_tool_result_with_nested(self) -> None:
        blocks: list[ContentBlock] = [
            ToolResultBlock(
                tool_use_id="tc-1",
                content=[TextBlock(text="nested")],
            ),
        ]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        result = restored[0]
        assert isinstance(result, ToolResultBlock)
        assert isinstance(result.content, list)
        assert len(result.content) == 1

    def test_round_trip_image(self) -> None:
        blocks: list[ContentBlock] = [
            ImageBlock(source=ImageSource(media_type="image/png", data="abc"))
        ]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert isinstance(restored[0], ImageBlock)

    def test_round_trip_thinking(self) -> None:
        blocks: list[ContentBlock] = [ThinkingBlock(thinking="hmm")]
        data = content_list_adapter.dump_json(blocks)
        restored = content_list_adapter.validate_json(data)
        assert isinstance(restored[0], ThinkingBlock)

    def test_empty_list(self) -> None:
        data = content_list_adapter.dump_json([])
        restored = content_list_adapter.validate_json(data)
        assert restored == []

    def test_invalid_discriminator_rejected(self) -> None:
        bad_json = json.dumps([{"type": "nonexistent", "data": "x"}]).encode()
        with pytest.raises(ValidationError):
            content_list_adapter.validate_json(bad_json)
