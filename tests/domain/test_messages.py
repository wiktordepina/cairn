"""Tests for Message — construction, streaming mutation, and serialization."""

from __future__ import annotations

import pytest

from cairn.domain._content import TextBlock, ToolUseBlock, content_list_adapter
from cairn.domain._messages import Message


class TestMessageConstruction:
    def test_auto_id(self) -> None:
        msg = Message(role="user")
        assert msg.id  # non-empty
        assert len(msg.id) == 32  # hex UUID

    def test_auto_datetime(self) -> None:
        msg = Message(role="user")
        assert msg.created_at is not None

    def test_defaults(self) -> None:
        msg = Message(role="assistant")
        assert msg.content == []
        assert msg.session_id == ""
        assert msg.idx == 0

    def test_explicit_values(self) -> None:
        msg = Message(
            role="user",
            id="msg-001",
            session_id="sess-001",
            idx=5,
        )
        assert msg.id == "msg-001"
        assert msg.session_id == "sess-001"
        assert msg.idx == 5


class TestAppendTextDelta:
    def test_creates_text_block_when_empty(self) -> None:
        msg = Message(role="assistant")
        msg.append_text_delta("hello")
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextBlock)
        assert msg.content[0].text == "hello"

    def test_accumulates_into_trailing_text(self) -> None:
        msg = Message(role="assistant")
        msg.append_text_delta("hel")
        msg.append_text_delta("lo")
        assert len(msg.content) == 1
        assert msg.content[0].text == "hello"  # type: ignore[union-attr]

    def test_creates_new_after_non_text(self) -> None:
        msg = Message(role="assistant")
        msg.content.append(ToolUseBlock(id="tc-1", name="x", input={}))
        msg.append_text_delta("after tool")
        assert len(msg.content) == 2
        assert isinstance(msg.content[1], TextBlock)
        assert msg.content[1].text == "after tool"


class TestGetText:
    def test_concatenates_all_text_blocks(self) -> None:
        msg = Message(role="assistant")
        msg.content = [
            TextBlock(text="part1"),
            ToolUseBlock(id="tc-1", name="x", input={}),
            TextBlock(text="part2"),
        ]
        assert msg.get_text() == "part1part2"

    def test_empty_message(self) -> None:
        msg = Message(role="assistant")
        assert msg.get_text() == ""


class TestToolUseStreaming:
    def test_full_lifecycle(self) -> None:
        msg = Message(role="assistant")
        msg.start_tool_use("tc-1", "web_fetch")
        msg.append_tool_input_delta("tc-1", '{"url":')
        msg.append_tool_input_delta("tc-1", '"https://x.com"}')
        msg.finalize_tool_use("tc-1")

        assert len(msg.content) == 1
        block = msg.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "tc-1"
        assert block.name == "web_fetch"
        assert block.input == {"url": "https://x.com"}

    def test_empty_input(self) -> None:
        msg = Message(role="assistant")
        msg.start_tool_use("tc-1", "noop")
        msg.finalize_tool_use("tc-1")

        block = msg.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input == {}

    def test_append_delta_unknown_id_raises(self) -> None:
        msg = Message(role="assistant")
        with pytest.raises(ValueError, match="No pending tool use"):
            msg.append_tool_input_delta("tc-999", "data")

    def test_finalize_unknown_id_raises(self) -> None:
        msg = Message(role="assistant")
        with pytest.raises(ValueError, match="No pending tool use"):
            msg.finalize_tool_use("tc-999")

    def test_pending_cleared_after_finalize(self) -> None:
        msg = Message(role="assistant")
        msg.start_tool_use("tc-1", "x")
        msg.finalize_tool_use("tc-1")
        # Second finalize should fail
        with pytest.raises(ValueError, match="No pending tool use"):
            msg.finalize_tool_use("tc-1")


class TestGetToolUse:
    def test_finds_by_id(self) -> None:
        msg = Message(role="assistant")
        msg.content = [
            TextBlock(text="before"),
            ToolUseBlock(id="tc-1", name="fetch", input={}),
            ToolUseBlock(id="tc-2", name="calc", input={}),
        ]
        result = msg.get_tool_use("tc-2")
        assert result.name == "calc"

    def test_missing_raises(self) -> None:
        msg = Message(role="assistant")
        with pytest.raises(ValueError, match="tc-999"):
            msg.get_tool_use("tc-999")


class TestSerialization:
    def test_content_json_round_trip(self) -> None:
        msg = Message(role="assistant")
        msg.append_text_delta("hello world")
        json_bytes = msg.content_json()

        restored = content_list_adapter.validate_json(json_bytes)
        assert len(restored) == 1
        assert isinstance(restored[0], TextBlock)
        assert restored[0].text == "hello world"

    def test_from_content_json(self) -> None:
        from datetime import UTC, datetime

        original = Message(role="user", id="msg-001", session_id="sess-001", idx=3)
        original.append_text_delta("test message")
        json_bytes = original.content_json()

        restored = Message.from_content_json(
            id="msg-001",
            session_id="sess-001",
            idx=3,
            role="user",
            content_json=json_bytes,
            created_at=datetime(2026, 4, 19, tzinfo=UTC),
        )
        assert restored.id == "msg-001"
        assert restored.role == "user"
        assert len(restored.content) == 1
        assert restored.get_text() == "test message"

    def test_from_content_json_string(self) -> None:
        from datetime import UTC, datetime

        json_str = '[{"type":"text","text":"from string"}]'
        msg = Message.from_content_json(
            id="msg-002",
            session_id="sess-001",
            idx=0,
            role="assistant",
            content_json=json_str,
            created_at=datetime(2026, 4, 19, tzinfo=UTC),
        )
        assert msg.get_text() == "from string"


class TestRepr:
    def test_repr(self) -> None:
        msg = Message(role="assistant", id="msg-001")
        msg.append_text_delta("hi")
        r = repr(msg)
        assert "msg-001" in r
        assert "assistant" in r
        assert "blocks=1" in r
