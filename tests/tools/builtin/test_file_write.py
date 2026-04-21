"""Tests for the ``file_write`` built-in tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.tools._errors import PathEscape, ToolError
from cairn.tools.builtin._file_write import make_file_write
from cairn.tools.security._sandbox import WorkspaceSandbox

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.orchestrator import TurnContext


@pytest.fixture
def sandbox(tmp_path: Path) -> WorkspaceSandbox:
    (tmp_path / "docs").mkdir()
    (tmp_path / "existing.txt").write_text("old\n", encoding="utf-8")
    return WorkspaceSandbox(root=tmp_path)


class TestMetadata:
    def test_metadata(self, sandbox: WorkspaceSandbox) -> None:
        t = make_file_write(sandbox)
        assert t.name == "file_write"
        assert t.risk_tier == 3
        assert t.side_effects == "write"
        assert t.approval_required is True
        assert t.tool_kind == "native"


class TestCreateMode:
    @pytest.mark.asyncio
    async def test_creates_new_file(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        result = await t.invoke(
            {"path": "new.txt", "content": "fresh", "mode": "create"}, turn_ctx
        )
        assert "Wrote 5 bytes" in result.content
        assert (tmp_path / "new.txt").read_text() == "fresh"

    @pytest.mark.asyncio
    async def test_create_fails_on_existing(
        self,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        with pytest.raises(ToolError, match="already exists"):
            await t.invoke(
                {"path": "existing.txt", "content": "x", "mode": "create"},
                turn_ctx,
            )


class TestOverwriteMode:
    @pytest.mark.asyncio
    async def test_overwrite_replaces_existing(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        await t.invoke(
            {"path": "existing.txt", "content": "new\n"}, turn_ctx
        )
        assert (tmp_path / "existing.txt").read_text() == "new\n"

    @pytest.mark.asyncio
    async def test_overwrite_creates_if_missing(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        await t.invoke(
            {"path": "docs/note.md", "content": "note"}, turn_ctx
        )
        assert (tmp_path / "docs" / "note.md").read_text() == "note"


class TestAppendMode:
    @pytest.mark.asyncio
    async def test_append_extends(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        await t.invoke(
            {"path": "existing.txt", "content": "more\n", "mode": "append"},
            turn_ctx,
        )
        assert (tmp_path / "existing.txt").read_text() == "old\nmore\n"

    @pytest.mark.asyncio
    async def test_append_creates_if_missing(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        await t.invoke(
            {"path": "newlog.txt", "content": "line1\n", "mode": "append"},
            turn_ctx,
        )
        assert (tmp_path / "newlog.txt").read_text() == "line1\n"


class TestSandboxViolations:
    @pytest.mark.asyncio
    async def test_rejects_absolute_path(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_write(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke(
                {"path": "/tmp/evil", "content": "x"}, turn_ctx
            )

    @pytest.mark.asyncio
    async def test_rejects_dotdot(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_write(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke(
                {"path": "../escape.txt", "content": "x"}, turn_ctx
            )


class TestParentDir:
    @pytest.mark.asyncio
    async def test_missing_parent_dir(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_file_write(sandbox)
        with pytest.raises(ToolError, match="Parent directory does not exist"):
            await t.invoke(
                {"path": "nonexistent/x.txt", "content": "y"}, turn_ctx
            )


class TestSizeCap:
    @pytest.mark.asyncio
    async def test_oversize_rejected(
        self,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox, max_bytes=100)
        with pytest.raises(ToolError, match="exceeds max_bytes"):
            await t.invoke(
                {"path": "big.txt", "content": "x" * 200}, turn_ctx
            )


class TestDirectoryOverwriteRejected:
    @pytest.mark.asyncio
    async def test_refuses_to_overwrite_directory(
        self,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        t = make_file_write(sandbox)
        with pytest.raises(ToolError, match="non-regular"):
            await t.invoke(
                {"path": "docs", "content": "x"}, turn_ctx
            )
