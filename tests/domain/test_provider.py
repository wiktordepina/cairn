"""Tests for provider types — request, events, and tool definitions."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from cairn.domain._enums import StopReason
from cairn.domain._messages import Message
from cairn.domain._provider import (
    BalanceInfo,
    GenerationId,
    MessageStop,
    ProviderRequest,
    SystemPromptSegment,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolDefinition,
    UsageEvent,
)


class TestToolDefinition:
    def test_construction(self) -> None:
        td = ToolDefinition(
            name="web_fetch",
            description="Fetch a web page",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        )
        assert td.name == "web_fetch"

    def test_frozen(self) -> None:
        td = ToolDefinition(name="x", description="x", input_schema={})
        with pytest.raises(ValidationError):
            td.name = "y"  # type: ignore[misc]


class TestProviderRequest:
    def test_construction(self) -> None:
        msg = Message(role="user")
        req = ProviderRequest(model="claude-opus-4-7", messages=[msg])
        assert req.model == "claude-opus-4-7"
        assert len(req.messages) == 1
        assert req.max_tokens == 4096  # default

    def test_frozen(self) -> None:
        req = ProviderRequest(model="x", messages=[])
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.model = "y"  # type: ignore[misc]

    def test_cache_flags_default_off(self) -> None:
        req = ProviderRequest(model="x", messages=[])
        assert req.cache_tools is False
        assert req.cache_last_message is False

    def test_cache_flags_settable(self) -> None:
        req = ProviderRequest(
            model="x",
            messages=[],
            cache_tools=True,
            cache_last_message=True,
        )
        assert req.cache_tools is True
        assert req.cache_last_message is True

    def test_system_as_string_back_compat(self) -> None:
        req = ProviderRequest(model="x", messages=[], system="hello")
        assert isinstance(req.system, str)
        assert req.system == "hello"

    def test_system_as_segment_list(self) -> None:
        segments = [
            SystemPromptSegment(text="identity", cacheable=True),
            SystemPromptSegment(text="memory", cacheable=True),
        ]
        req = ProviderRequest(model="x", messages=[], system=segments)
        assert isinstance(req.system, list)
        assert len(req.system) == 2
        assert all(isinstance(s, SystemPromptSegment) for s in req.system)

    def test_new_optional_fields_default_none_or_off(self) -> None:
        req = ProviderRequest(model="x", messages=[])
        assert req.reasoning_effort is None
        assert req.tool_choice is None
        assert req.disable_parallel_tool_use is False
        assert req.prompt_cache_key is None

    def test_new_optional_fields_settable(self) -> None:
        req = ProviderRequest(
            model="x",
            messages=[],
            reasoning_effort="high",
            tool_choice="auto",
            disable_parallel_tool_use=True,
            prompt_cache_key="cairn:abc-123",
        )
        assert req.reasoning_effort == "high"
        assert req.tool_choice == "auto"
        assert req.disable_parallel_tool_use is True
        assert req.prompt_cache_key == "cairn:abc-123"

    def test_tool_choice_tuple_for_named_tool(self) -> None:
        req = ProviderRequest(model="x", messages=[], tool_choice=("tool", "web_fetch"))
        assert req.tool_choice == ("tool", "web_fetch")


class TestSystemPromptSegment:
    def test_default_not_cacheable(self) -> None:
        seg = SystemPromptSegment(text="hello")
        assert seg.cacheable is False

    def test_cacheable_settable(self) -> None:
        seg = SystemPromptSegment(text="hello", cacheable=True)
        assert seg.cacheable is True

    def test_frozen(self) -> None:
        seg = SystemPromptSegment(text="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            seg.text = "world"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SystemPromptSegment(text="x", cacheable=True)
        b = SystemPromptSegment(text="x", cacheable=True)
        assert a == b
        c = SystemPromptSegment(text="x", cacheable=False)
        assert a != c


class TestProviderEvents:
    def test_text_delta(self) -> None:
        ev = TextDelta(text="hello")
        assert ev.text == "hello"

    def test_tool_call_start(self) -> None:
        ev = ToolCallStart(id="tc-1", name="fetch")
        assert ev.id == "tc-1"
        assert ev.name == "fetch"

    def test_tool_call_delta(self) -> None:
        ev = ToolCallDelta(id="tc-1", input_delta='{"url":')
        assert ev.input_delta == '{"url":'

    def test_tool_call_end(self) -> None:
        ev = ToolCallEnd(id="tc-1")
        assert ev.id == "tc-1"

    def test_usage_event(self) -> None:
        ev = UsageEvent(input_tokens=100, output_tokens=50)
        assert ev.cache_read_tokens == 0  # default

    def test_usage_event_with_cache(self) -> None:
        ev = UsageEvent(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=30,
            cache_write_tokens=10,
        )
        assert ev.cache_read_tokens == 30

    def test_usage_event_new_breakdown_fields_default_zero(self) -> None:
        ev = UsageEvent(input_tokens=100, output_tokens=50)
        assert ev.reasoning_tokens == 0
        assert ev.cache_discount_usd == 0.0

    def test_usage_event_with_reasoning_and_discount(self) -> None:
        ev = UsageEvent(
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=150,
            cache_discount_usd=0.0042,
        )
        assert ev.reasoning_tokens == 150
        assert ev.cache_discount_usd == pytest.approx(0.0042)

    def test_generation_id_event(self) -> None:
        ev = GenerationId(id="gen-abc-123")
        assert ev.id == "gen-abc-123"
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.id = "y"  # type: ignore[misc]

    def test_message_stop(self) -> None:
        ev = MessageStop(stop_reason=StopReason.END_TURN)
        assert ev.stop_reason == StopReason.END_TURN

    def test_all_events_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            TextDelta(text="x").text = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            ToolCallStart(id="1", name="x").id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            ToolCallEnd(id="1").id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            UsageEvent(input_tokens=0, output_tokens=0).input_tokens = 1  # type: ignore[misc]


class TestBalanceInfo:
    def test_construction_minimal(self) -> None:
        b = BalanceInfo(
            currency="USD", total=10.0, used=4.0, remaining=6.0, source="/api/v1/credits"
        )
        assert b.currency == "USD"
        assert b.remaining == 6.0
        assert b.granted is None

    def test_construction_with_grant(self) -> None:
        b = BalanceInfo(
            currency="CNY",
            total=110.0,
            used=22.6,
            remaining=87.4,
            granted=10.0,
            source="/user/balance",
        )
        assert b.granted == 10.0

    def test_frozen(self) -> None:
        b = BalanceInfo(currency="USD", total=1.0, used=0.0, remaining=1.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            b.currency = "EUR"  # type: ignore[misc]
