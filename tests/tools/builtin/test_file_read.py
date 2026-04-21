"""Tests for the ``file_read`` built-in tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.domain._content import ToolResultBlock
from cairn.tools._errors import PathEscape, ToolError
from cairn.tools.builtin._file_read import (
    DEFAULT_MAX_BYTES,
    make_file_read,
)
from cairn.tools.security._sandbox import WorkspaceSandbox

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.orchestrator import TurnContext


@pytest.fixture
def sandbox(tmp_path: Path) -> WorkspaceSandbox:
    (tmp_path / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "greeting.txt").write_text("hi", encoding="utf-8")
    return WorkspaceSandbox(root=tmp_path)


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_reads_text_file(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "hello.txt"}, turn_ctx)
        assert isinstance(result, ToolResultBlock)
        assert result.content == "hello\nworld\n"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_reads_nested_path(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "nested/greeting.txt"}, turn_ctx)
        assert result.content == "hi"


class TestMetadata:
    def test_metadata(self, sandbox: WorkspaceSandbox) -> None:
        t = make_file_read(sandbox)
        assert t.name == "file_read"
        assert t.risk_tier == 1
        assert t.side_effects == "read"
        assert t.approval_required is False
        assert t.timeout_s == 10.0
        assert t.tool_kind == "native"
        props = t.input_schema["properties"]
        assert isinstance(props, dict)
        assert "path" in props


class TestErrors:
    @pytest.mark.asyncio
    async def test_missing_file(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(ToolError, match="not found"):
            await t.invoke({"path": "nope.txt"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_directory_path(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(ToolError, match="Not a regular file"):
            await t.invoke({"path": "nested"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_outside_sandbox_absolute(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke({"path": "/etc/passwd"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_outside_sandbox_dotdot(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke({"path": "../etc/passwd"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_symlink_escape(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        outside = tmp_path.parent / "target_outside"
        outside.write_text("leaked", encoding="utf-8")
        link = tmp_path / "escape.txt"
        link.symlink_to(outside)
        t = make_file_read(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke({"path": "escape.txt"}, turn_ctx)


class TestBinary:
    @pytest.mark.asyncio
    async def test_binary_returns_summary(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        payload = b"\x89PNG\x00\r\n\x1a\n" + b"\x01\x02\x03" * 20
        (tmp_path / "pic.png").write_bytes(payload)
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "pic.png"}, turn_ctx)
        text = result.content
        assert isinstance(text, str)
        assert text.startswith("[binary: ")
        assert f"{len(payload)} bytes" in text
        assert "sha256:" in text
        # MIME is inferred from the extension.
        assert "image/png" in text

    @pytest.mark.asyncio
    async def test_binary_unknown_extension(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        (tmp_path / "blob").write_bytes(b"\x00\x01\x02\x03")
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "blob"}, turn_ctx)
        assert "application/octet-stream" in result.content


class TestTruncation:
    @pytest.mark.asyncio
    async def test_large_text_is_truncated(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        big = "a" * (DEFAULT_MAX_BYTES + 500)
        (tmp_path / "big.txt").write_text(big, encoding="utf-8")
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "big.txt"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert content.startswith("a")
        assert "[truncated: " in content
        assert f"{DEFAULT_MAX_BYTES + 500} bytes total" in content
        assert len(content) < len(big)

    @pytest.mark.asyncio
    async def test_small_files_not_truncated(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        (tmp_path / "small.txt").write_text("short", encoding="utf-8")
        t = make_file_read(sandbox)
        result = await t.invoke({"path": "small.txt"}, turn_ctx)
        assert result.content == "short"

    @pytest.mark.asyncio
    async def test_configurable_max_bytes(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        (tmp_path / "mid.txt").write_text("x" * 200, encoding="utf-8")
        t = make_file_read(sandbox, max_bytes=100)
        result = await t.invoke({"path": "mid.txt"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert "[truncated: 200 bytes total]" in content


class TestArgValidation:
    @pytest.mark.asyncio
    async def test_missing_path(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(Exception):  # noqa: PT011, B017 — pydantic ValidationError
            await t.invoke({}, turn_ctx)

    @pytest.mark.asyncio
    async def test_empty_path(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_read(sandbox)
        with pytest.raises(PathEscape, match="Empty"):
            await t.invoke({"path": ""}, turn_ctx)
