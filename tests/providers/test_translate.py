"""Tests for shared translation helpers."""

from __future__ import annotations

from cairn.domain._content import ImageBlock, ImageSource, TextBlock, ToolResultBlock, ToolUseBlock
from cairn.domain._enums import StopReason
from cairn.domain._messages import Message
from cairn.providers._translate import (
    extract_text,
    get_images,
    get_tool_results,
    get_tool_uses,
    has_tool_results,
    has_tool_uses,
    map_anthropic_stop_reason,
    map_openai_stop_reason,
)


class TestExtractText:
    def test_single_text(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="hello")]
        assert extract_text(msg) == "hello"

    def test_multiple_text(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="a"), TextBlock(text="b")]
        assert extract_text(msg) == "ab"

    def test_mixed_content(self) -> None:
        msg = Message(role="assistant")
        msg.content = [
            TextBlock(text="before"),
            ToolUseBlock(id="tc-1", name="x", input={}),
            TextBlock(text="after"),
        ]
        assert extract_text(msg) == "beforeafter"


class TestHasHelpers:
    def test_has_tool_uses(self) -> None:
        msg = Message(role="assistant")
        msg.content = [ToolUseBlock(id="tc-1", name="x", input={})]
        assert has_tool_uses(msg) is True

    def test_no_tool_uses(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        assert has_tool_uses(msg) is False

    def test_has_tool_results(self) -> None:
        msg = Message(role="user")
        msg.content = [ToolResultBlock(tool_use_id="tc-1", content="done")]
        assert has_tool_results(msg) is True


class TestGetHelpers:
    def test_get_tool_uses(self) -> None:
        msg = Message(role="assistant")
        msg.content = [
            TextBlock(text="x"),
            ToolUseBlock(id="tc-1", name="a", input={}),
            ToolUseBlock(id="tc-2", name="b", input={}),
        ]
        uses = get_tool_uses(msg)
        assert len(uses) == 2
        assert uses[0].name == "a"

    def test_get_tool_results(self) -> None:
        msg = Message(role="user")
        msg.content = [ToolResultBlock(tool_use_id="tc-1", content="result")]
        results = get_tool_results(msg)
        assert len(results) == 1

    def test_get_images(self) -> None:
        msg = Message(role="user")
        msg.content = [ImageBlock(source=ImageSource(media_type="image/png", data="abc"))]
        images = get_images(msg)
        assert len(images) == 1


class TestStopReasonMapping:
    def test_anthropic_end_turn(self) -> None:
        assert map_anthropic_stop_reason("end_turn") == StopReason.END_TURN

    def test_anthropic_max_tokens(self) -> None:
        assert map_anthropic_stop_reason("max_tokens") == StopReason.MAX_TOKENS

    def test_anthropic_tool_use(self) -> None:
        assert map_anthropic_stop_reason("tool_use") == StopReason.TOOL_USE

    def test_anthropic_none(self) -> None:
        assert map_anthropic_stop_reason(None) == StopReason.END_TURN

    def test_anthropic_unknown(self) -> None:
        assert map_anthropic_stop_reason("unknown") == StopReason.END_TURN

    def test_openai_stop(self) -> None:
        assert map_openai_stop_reason("stop") == StopReason.END_TURN

    def test_openai_length(self) -> None:
        assert map_openai_stop_reason("length") == StopReason.MAX_TOKENS

    def test_openai_tool_calls(self) -> None:
        assert map_openai_stop_reason("tool_calls") == StopReason.TOOL_USE

    def test_openai_none(self) -> None:
        assert map_openai_stop_reason(None) == StopReason.END_TURN
