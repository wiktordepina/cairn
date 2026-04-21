"""Tests for the `grep` built-in tool.

Exercises the pure-Python fallback (primary path on CI) and the
ripgrep subprocess path via mocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.tools._errors import PathEscape, ToolError
from cairn.tools.builtin import _grep
from cairn.tools.builtin._grep import make_grep
from cairn.tools.security._sandbox import WorkspaceSandbox

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.orchestrator import TurnContext


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkspaceSandbox:
    # Force Python fallback by default — tests opt into ripgrep explicitly.
    monkeypatch.setattr(_grep, "_ripgrep_path", lambda: None)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def main():\n    print('hello')\n    return 0\n", encoding="utf-8"
    )
    (tmp_path / "src" / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project\nhello there\n", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.py").write_text("hello secret\n", encoding="utf-8")
    return WorkspaceSandbox(root=tmp_path)


class TestMetadata:
    def test_metadata(self, sandbox: WorkspaceSandbox) -> None:
        t = make_grep(sandbox)
        assert t.name == "grep"
        assert t.risk_tier == 1
        assert t.side_effects == "read"
        assert t.approval_required is False


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_finds_matches(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "hello", "path": "."}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert "README.md:2:hello there" in content
        assert "src/main.py:2:    print('hello')" in content

    @pytest.mark.asyncio
    async def test_no_matches(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "zzznonexistent", "path": "."}, turn_ctx)
        assert result.content == "[no matches]"

    @pytest.mark.asyncio
    async def test_glob_filter(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "hello", "path": ".", "glob": "*.md"}, turn_ctx)
        content = result.content
        assert "README.md" in content
        assert "main.py" not in content

    @pytest.mark.asyncio
    async def test_searches_single_file(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "helper", "path": "src/util.py"}, turn_ctx)
        assert "src/util.py:1:def helper" in result.content

    @pytest.mark.asyncio
    async def test_regex_pattern(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": r"^def \w+", "path": "src"}, turn_ctx)
        content = result.content
        assert "src/main.py" in content
        assert "src/util.py" in content


class TestHiddenDirs:
    @pytest.mark.asyncio
    async def test_skips_hidden_dirs(
        self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext
    ) -> None:
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "hello", "path": "."}, turn_ctx)
        # `.hidden/secret.py` contains "hello" but must be skipped.
        assert ".hidden" not in result.content


class TestBinarySkip:
    @pytest.mark.asyncio
    async def test_skips_binary(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        (tmp_path / "src" / "data.bin").write_bytes(b"hello\x00\x01binary\x00")
        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "hello", "path": "src"}, turn_ctx)
        assert "data.bin" not in result.content


class TestLargeFileSkip:
    @pytest.mark.asyncio
    async def test_skips_oversize(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        big = "hello\n" * 1000  # 6000 bytes
        (tmp_path / "huge.txt").write_text(big, encoding="utf-8")
        t = make_grep(sandbox, max_file_bytes=100)
        result = await t.invoke({"pattern": "hello", "path": "huge.txt"}, turn_ctx)
        assert result.content == "[no matches]"


class TestErrorCases:
    @pytest.mark.asyncio
    async def test_missing_path(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        with pytest.raises(ToolError, match="does not exist"):
            await t.invoke({"pattern": "x", "path": "nope"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_invalid_regex(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        with pytest.raises(ToolError, match="Invalid regex"):
            await t.invoke({"pattern": "(unclosed", "path": "."}, turn_ctx)

    @pytest.mark.asyncio
    async def test_sandbox_escape(self, sandbox: WorkspaceSandbox, turn_ctx: TurnContext) -> None:
        t = make_grep(sandbox)
        with pytest.raises(PathEscape):
            await t.invoke({"pattern": "x", "path": "/etc"}, turn_ctx)


class TestMatchCap:
    @pytest.mark.asyncio
    async def test_truncates_at_cap(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
    ) -> None:
        lines = "\n".join(f"hello {i}" for i in range(50))
        (tmp_path / "many.txt").write_text(lines, encoding="utf-8")
        t = make_grep(sandbox, max_matches=5)
        result = await t.invoke({"pattern": "hello", "path": "many.txt"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert "[truncated: match cap 5 reached]" in content
        # Five result lines + the truncation suffix.
        assert content.count("\n") == 5


class TestRipgrepPath:
    @pytest.mark.asyncio
    async def test_uses_ripgrep_when_available(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pretend rg is present and stub out the subprocess search.
        monkeypatch.setattr(_grep, "_ripgrep_path", lambda: "/usr/bin/rg")

        calls: list[dict[str, object]] = []

        async def fake_ripgrep_search(**kwargs: object) -> str:
            calls.append(kwargs)
            return "src/main.py:2:    print('hello')"

        monkeypatch.setattr(_grep, "_ripgrep_search", fake_ripgrep_search)

        t = make_grep(sandbox)
        result = await t.invoke({"pattern": "hello", "path": "src"}, turn_ctx)
        assert len(calls) == 1
        assert calls[0]["rg_path"] == "/usr/bin/rg"
        assert calls[0]["pattern"] == "hello"
        assert "src/main.py:2" in result.content

    @pytest.mark.asyncio
    async def test_ripgrep_subprocess_shape(
        self,
        tmp_path: Path,
        sandbox: WorkspaceSandbox,
        turn_ctx: TurnContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_grep, "_ripgrep_path", lambda: "/usr/bin/rg")

        captured_argv: list[list[str]] = []

        class FakeProc:
            returncode = 1  # no matches

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

            async def wait(self) -> None:
                return None

            def kill(self) -> None:
                return None

        async def fake_create_subprocess_exec(
            *argv: str,
            **kwargs: object,  # noqa: ARG001
        ) -> FakeProc:
            captured_argv.append(list(argv))
            return FakeProc()

        monkeypatch.setattr(
            _grep.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        t = make_grep(sandbox, max_matches=150, max_file_bytes=500_000)
        await t.invoke({"pattern": "hello", "path": "src", "glob": "*.py"}, turn_ctx)
        argv = captured_argv[0]
        assert argv[0] == "/usr/bin/rg"
        # Safety flags — belt-and-braces against symlink follows.
        assert "--no-follow" in argv
        assert "--max-filesize=500000" in argv
        assert "--max-count=150" in argv
        assert "--glob" in argv
        assert "-e" in argv
        # Pattern comes after `-e`.
        assert argv[argv.index("-e") + 1] == "hello"
