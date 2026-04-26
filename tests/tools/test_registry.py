"""Tests for DefaultToolRegistry scoping."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from cairn.tools import DefaultToolRegistry, tool

if TYPE_CHECKING:
    from cairn.domain import Session
    from cairn.orchestrator import TurnContext


class NoArgs(BaseModel):
    pass


def _make_tool(name: str):
    @tool(
        name=name,
        description=f"{name} tool",
        risk_tier=1,
        side_effects="read",
        timeout_s=10.0,
        args_model=NoArgs,
    )
    async def t(args: NoArgs, ctx: TurnContext) -> str:  # noqa: ARG001
        return "ok"

    return t


class TestScoping:
    def test_companion_sees_all(self, companion_session: Session) -> None:
        a, b = _make_tool("a"), _make_tool("b")
        reg = DefaultToolRegistry(companion_tools=[a, b])
        defs = reg.for_session(companion_session)
        assert {d.name for d in defs} == {"a", "b"}

    def test_ephemeral_default_empty(self, ephemeral_session: Session) -> None:
        reg = DefaultToolRegistry(companion_tools=[_make_tool("a")])
        assert reg.for_session(ephemeral_session) == []

    def test_ephemeral_with_allowlist(self, ephemeral_session: Session) -> None:
        a, b = _make_tool("a"), _make_tool("b")
        reg = DefaultToolRegistry(companion_tools=[a, b], ephemeral_allowlist=["a"])
        defs = reg.for_session(ephemeral_session)
        assert {d.name for d in defs} == {"a"}

    def test_persona_honours_allowlist(self, persona_session: Session) -> None:
        a, b, c = _make_tool("a"), _make_tool("b"), _make_tool("c")
        reg = DefaultToolRegistry(
            companion_tools=[a, b, c],
            persona_allowlists={"work-assistant": ["a", "c"]},
        )
        defs = reg.for_session(persona_session)
        assert {d.name for d in defs} == {"a", "c"}

    def test_persona_without_allowlist_sees_nothing(self, persona_session: Session) -> None:
        reg = DefaultToolRegistry(companion_tools=[_make_tool("a")])
        assert reg.for_session(persona_session) == []

    def test_mcp_tools_visible_to_companion(self, companion_session: Session) -> None:
        native = _make_tool("native-one")
        mcp = _make_tool("mcp-one")
        reg = DefaultToolRegistry(companion_tools=[native], mcp_tools=[mcp])
        defs = reg.for_session(companion_session)
        assert {d.name for d in defs} == {"native-one", "mcp-one"}


class TestGet:
    def test_returns_registered_tool(self) -> None:
        a = _make_tool("a")
        reg = DefaultToolRegistry(companion_tools=[a])
        got = reg.get("a")
        assert got is a

    def test_returns_none_for_unknown(self) -> None:
        reg = DefaultToolRegistry()
        assert reg.get("nope") is None

    def test_finds_mcp_tools(self) -> None:
        mcp = _make_tool("mcp-tool")
        reg = DefaultToolRegistry(mcp_tools=[mcp])
        assert reg.get("mcp-tool") is mcp


class TestDuplicateRejection:
    def test_duplicate_companion_names(self) -> None:
        a1 = _make_tool("dup")
        a2 = _make_tool("dup")
        with pytest.raises(ValueError, match="Duplicate tool name"):
            DefaultToolRegistry(companion_tools=[a1, a2])

    def test_duplicate_across_companion_and_mcp(self) -> None:
        a = _make_tool("dup")
        b = _make_tool("dup")
        with pytest.raises(ValueError, match="Duplicate tool name"):
            DefaultToolRegistry(companion_tools=[a], mcp_tools=[b])


class TestNames:
    def test_empty_registry_has_no_names(self) -> None:
        assert DefaultToolRegistry().names() == frozenset()

    def test_collects_companion_and_mcp_names(self) -> None:
        a = _make_tool("a")
        b = _make_tool("b")
        m = _make_tool("m")
        reg = DefaultToolRegistry(companion_tools=[a, b], mcp_tools=[m])
        assert reg.names() == frozenset({"a", "b", "m"})
