"""Tests for turn-block detection."""

from __future__ import annotations

from cairn.compaction import TurnBlock, iter_turn_blocks
from cairn.domain import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _user(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_text(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


def _assistant_with_tool(text: str, tool_id: str, name: str = "grep") -> Message:
    return Message(
        role="assistant",
        content=[
            TextBlock(text=text),
            ToolUseBlock(id=tool_id, name=name, input={"q": "x"}),
        ],
    )


def _user_tool_result(tool_id: str, result: str = "ok") -> Message:
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=result)],
    )


def test_empty_sequence_yields_nothing() -> None:
    assert list(iter_turn_blocks([])) == []


def test_plain_text_turns_one_block_each() -> None:
    messages = [
        _user("hi"),
        _assistant_text("hello"),
        _user("how are you"),
        _assistant_text("fine"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert blocks == [
        TurnBlock(start=0, end=2, message_count=2, has_tool_pair=False),
        TurnBlock(start=2, end=4, message_count=2, has_tool_pair=False),
    ]


def test_single_tool_pair_folded_into_enclosing_turn() -> None:
    messages = [
        _user("grep foo"),
        _assistant_with_tool("let me search", "t1"),
        _user_tool_result("t1", "match"),
        _assistant_text("found it"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert len(blocks) == 1
    assert blocks[0] == TurnBlock(start=0, end=4, message_count=4, has_tool_pair=True)


def test_multiple_tool_calls_in_one_turn_stay_together() -> None:
    messages = [
        _user("check two files"),
        _assistant_with_tool("reading", "t1"),
        _user_tool_result("t1", "alpha"),
        _assistant_with_tool("reading more", "t2"),
        _user_tool_result("t2", "beta"),
        _assistant_text("done"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert len(blocks) == 1
    assert blocks[0].start == 0
    assert blocks[0].end == 6
    assert blocks[0].has_tool_pair is True


def test_new_real_user_message_opens_new_block() -> None:
    messages = [
        _user("grep foo"),
        _assistant_with_tool("searching", "t1"),
        _user_tool_result("t1", "hit"),
        _assistant_text("found"),
        _user("now edit it"),
        _assistant_text("done"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert [(b.start, b.end, b.has_tool_pair) for b in blocks] == [
        (0, 4, True),
        (4, 6, False),
    ]


def test_in_flight_user_without_assistant_reply() -> None:
    messages = [
        _user("first"),
        _assistant_text("first reply"),
        _user("second (no reply yet)"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert [(b.start, b.end) for b in blocks] == [(0, 2), (2, 3)]


def test_block_starts_only_on_non_tool_result_user_message() -> None:
    """A user message that is purely tool results should not open a new block."""
    messages = [
        _user("start"),
        _assistant_with_tool("thinking", "t1"),
        _user_tool_result("t1"),
        _assistant_text("and done"),
    ]
    blocks = list(iter_turn_blocks(messages))
    assert len(blocks) == 1
    assert blocks[0].has_tool_pair is True


def test_every_message_belongs_to_exactly_one_block() -> None:
    messages = [
        _user("a"),
        _assistant_text("A"),
        _user("b"),
        _assistant_with_tool("B", "t1"),
        _user_tool_result("t1"),
        _user("c"),
    ]
    blocks = list(iter_turn_blocks(messages))
    total = sum(b.message_count for b in blocks)
    assert total == len(messages)
    spans = [(b.start, b.end) for b in blocks]
    for (_, end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert end == next_start
