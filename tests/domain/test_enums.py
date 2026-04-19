"""Tests for domain enums."""

from __future__ import annotations

from cairn.domain._enums import (
    ErrorClass,
    MemoryClass,
    MemoryEntryType,
    SessionType,
    StopReason,
    ToolCallStatus,
    UsageOperation,
)


class TestSessionType:
    def test_members(self) -> None:
        assert set(SessionType) == {
            SessionType.COMPANION,
            SessionType.PERSONA,
            SessionType.EPHEMERAL,
        }

    def test_string_coercion(self) -> None:
        assert SessionType("companion") == SessionType.COMPANION


class TestStopReason:
    def test_members(self) -> None:
        assert set(StopReason) == {
            StopReason.END_TURN,
            StopReason.MAX_TOKENS,
            StopReason.TOOL_USE,
        }

    def test_string_value(self) -> None:
        assert StopReason.END_TURN == "end_turn"


class TestUsageOperation:
    def test_member_count(self) -> None:
        assert len(UsageOperation) == 7


class TestToolCallStatus:
    def test_member_count(self) -> None:
        assert len(ToolCallStatus) == 8

    def test_lifecycle_values(self) -> None:
        assert ToolCallStatus("pending") == ToolCallStatus.PENDING
        assert ToolCallStatus("completed") == ToolCallStatus.COMPLETED


class TestErrorClass:
    def test_member_count(self) -> None:
        assert len(ErrorClass) == 4


class TestMemoryEntryType:
    def test_member_count(self) -> None:
        assert len(MemoryEntryType) == 6


class TestMemoryClass:
    def test_members(self) -> None:
        assert set(MemoryClass) == {MemoryClass.SEMANTIC, MemoryClass.EPISODIC}
