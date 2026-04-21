"""Tests for the ``@tool`` decorator."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, Field

from cairn.domain._content import TextBlock, ToolResultBlock
from cairn.orchestrator._protocols import Tool
from cairn.tools import tool

if TYPE_CHECKING:
    from cairn.orchestrator import TurnContext


class SimpleArgs(BaseModel):
    message: str = Field(description="Text to echo")


class WriteArgs(BaseModel):
    path: str
    content: str


class TestValidTools:
    def test_creates_tool(self) -> None:
        @tool(
            name="echo",
            description="Echo a message",
            risk_tier=0,
            side_effects="none",
            timeout_s=5.0,
            args_model=SimpleArgs,
        )
        async def echo(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return args.message

        assert isinstance(echo, Tool)
        assert echo.name == "echo"
        assert echo.description == "Echo a message"
        assert echo.risk_tier == 0
        assert echo.side_effects == "none"
        assert echo.timeout_s == 5.0
        assert echo.tool_kind == "native"
        assert echo.approval_required is False  # default for tier 0

    def test_generates_input_schema(self) -> None:
        @tool(
            name="echo",
            description="Echo a message",
            risk_tier=0,
            side_effects="none",
            timeout_s=5.0,
            args_model=SimpleArgs,
        )
        async def echo(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return args.message

        schema = echo.input_schema
        assert schema["type"] == "object"
        assert "message" in schema["properties"]
        assert schema["properties"]["message"]["description"] == "Text to echo"

    def test_tier_3_defaults_to_approval_required(self) -> None:
        @tool(
            name="writer",
            description="Write a file",
            risk_tier=3,
            side_effects="write",
            timeout_s=10.0,
            args_model=WriteArgs,
        )
        async def writer(args: WriteArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return "wrote"

        assert writer.approval_required is True

    def test_explicit_approval_required_overrides(self) -> None:
        @tool(
            name="writer",
            description="Write a file",
            risk_tier=3,
            side_effects="write",
            timeout_s=10.0,
            args_model=WriteArgs,
            approval_required=False,  # override the default
        )
        async def writer(args: WriteArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return "wrote"

        assert writer.approval_required is False


class TestValidationRules:
    def test_rejects_tier_5(self) -> None:
        with pytest.raises(ValueError, match="risk_tier"):

            @tool(
                name="shell",
                description="x",
                risk_tier=5,
                side_effects="write",
                timeout_s=10.0,
                args_model=SimpleArgs,
            )
            async def shell(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
                return ""

    def test_rejects_tier_0_with_write_side_effects(self) -> None:
        with pytest.raises(ValueError, match="read-only"):

            @tool(
                name="x",
                description="x",
                risk_tier=0,
                side_effects="write",
                timeout_s=10.0,
                args_model=WriteArgs,
            )
            async def x(args: WriteArgs, ctx: TurnContext) -> str:  # noqa: ARG001
                return ""

    def test_rejects_tier_3_with_none_side_effects(self) -> None:
        with pytest.raises(ValueError, match="mutating"):

            @tool(
                name="x",
                description="x",
                risk_tier=3,
                side_effects="none",
                timeout_s=10.0,
                args_model=WriteArgs,
            )
            async def x(args: WriteArgs, ctx: TurnContext) -> str:  # noqa: ARG001
                return ""

    def test_allows_tier_2_read(self) -> None:
        # Tier 2 with side_effects="read" is the web_fetch case — allowed.
        @tool(
            name="fetch",
            description="fetch",
            risk_tier=2,
            side_effects="read",
            timeout_s=30.0,
            args_model=SimpleArgs,
        )
        async def fetch(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return ""

        assert fetch.risk_tier == 2


class TestInvocation:
    @pytest.mark.asyncio
    async def test_parses_args_and_returns_result(self, turn_ctx: TurnContext) -> None:
        @tool(
            name="echo",
            description="echo",
            risk_tier=0,
            side_effects="none",
            timeout_s=5.0,
            args_model=SimpleArgs,
        )
        async def echo(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return f"echoed: {args.message}"

        result = await echo.invoke({"message": "hi"}, turn_ctx)
        assert isinstance(result, ToolResultBlock)
        assert result.content == "echoed: hi"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_accepts_list_content_return(self, turn_ctx: TurnContext) -> None:
        @tool(
            name="rich",
            description="rich",
            risk_tier=0,
            side_effects="none",
            timeout_s=5.0,
            args_model=SimpleArgs,
        )
        async def rich(args: SimpleArgs, ctx: TurnContext):  # noqa: ARG001
            return [TextBlock(text=args.message)]

        result = await rich.invoke({"message": "hi"}, turn_ctx)
        assert isinstance(result.content, list)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "hi"

    @pytest.mark.asyncio
    async def test_rejects_malformed_args(self, turn_ctx: TurnContext) -> None:
        @tool(
            name="echo",
            description="echo",
            risk_tier=0,
            side_effects="none",
            timeout_s=5.0,
            args_model=SimpleArgs,
        )
        async def echo(args: SimpleArgs, ctx: TurnContext) -> str:  # noqa: ARG001
            return args.message

        with pytest.raises(Exception):  # noqa: PT011, B017 — pydantic ValidationError
            await echo.invoke({}, turn_ctx)  # missing required 'message'
