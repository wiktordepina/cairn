"""``grep`` — Tier 1, regex search confined to the workspace.

Uses ``ripgrep`` if available on ``PATH``; otherwise falls back to a
pure-Python walker built on ``re`` + ``pathlib``. Both paths:

- Resolve the caller's search root through the workspace sandbox.
- Skip binary files (null-byte detection on the first 8 KB).
- Skip files larger than ``max_file_bytes``.
- Cap total matches at ``max_matches`` (default 200) and append a
  truncation note when the cap bites.

Output is a plain-text block of ``path:lineno:line`` entries, one per
match — easy for the model to consume without further parsing.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from cairn.tools._decorator import tool
from cairn.tools._errors import ToolError

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._protocols import Tool
    from cairn.tools.security._sandbox import WorkspaceSandbox


DEFAULT_MAX_MATCHES = 200
DEFAULT_MAX_FILE_BYTES = 1_000_000
_BINARY_PROBE_BYTES = 8_192
_SUBPROCESS_TIMEOUT_S = 20.0


class GrepArgs(BaseModel):
    pattern: str = Field(description="Python-flavoured regex pattern.")
    path: str = Field(
        default=".",
        description=(
            "Path relative to the workspace root to search under. Defaults to the workspace root."
        ),
    )
    glob: str | None = Field(
        default=None,
        description=(
            "Optional glob filter applied to file basenames, e.g. '*.py'. "
            "Matches against the filename only, not the full path."
        ),
    )


def _ripgrep_path() -> str | None:
    """Resolved at call time so tests can monkeypatch ``shutil.which``."""
    return shutil.which("rg")


def _looks_binary(head: bytes) -> bool:
    return b"\x00" in head


def _is_hidden(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


async def _python_search(
    *,
    search_root: Path,
    sandbox_root: Path,
    pattern: str,
    glob: str | None,
    max_matches: int,
    max_file_bytes: int,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"Invalid regex: {exc}") from exc

    def _walk() -> tuple[list[str], bool]:
        matches: list[str] = []
        truncated = False

        if search_root.is_file():
            files: list[Path] = [search_root]
        else:
            files = [p for p in search_root.rglob("*") if p.is_file()]

        for file in files:
            if truncated:
                break
            if _is_hidden(file, sandbox_root):
                continue
            if glob is not None and not file.match(glob):
                continue
            try:
                size = file.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                continue
            try:
                with file.open("rb") as f:
                    head = f.read(_BINARY_PROBE_BYTES)
                    if _looks_binary(head):
                        continue
                    rest = f.read()
            except OSError:
                continue

            try:
                text = (head + rest).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue

            try:
                rel = file.relative_to(sandbox_root)
            except ValueError:
                rel = file
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{rel}:{lineno}:{line}")
                    if len(matches) >= max_matches:
                        truncated = True
                        break
        return matches, truncated

    matches, truncated = await asyncio.to_thread(_walk)
    return _format_result(matches, truncated, max_matches)


async def _ripgrep_search(
    *,
    rg_path: str,
    search_root: Path,
    sandbox_root: Path,
    pattern: str,
    glob: str | None,
    max_matches: int,
    max_file_bytes: int,
) -> str:
    argv: list[str] = [
        rg_path,
        "--no-heading",
        "--no-color",
        "--line-number",
        "--with-filename",
        "--no-follow",
        f"--max-filesize={max_file_bytes}",
        f"--max-count={max_matches}",
        "-e",
        pattern,
    ]
    if glob is not None:
        argv.extend(["--glob", glob])
    argv.append("--")
    argv.append(str(search_root))

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"ripgrep not found at {rg_path}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=_SUBPROCESS_TIMEOUT_S
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ToolError("grep subprocess timed out") from exc

    # rg exits 0 when matches found, 1 when none, 2+ on error.
    if proc.returncode not in (0, 1):
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        raise ToolError(f"ripgrep failed ({proc.returncode}): {stderr}")

    stdout = stdout_b.decode("utf-8", errors="replace")

    matches: list[str] = []
    truncated = False
    for raw in stdout.splitlines():
        # Rewrite absolute paths into sandbox-relative form for the model.
        # rg's output shape: "<abs_path>:<lineno>:<line>".
        head, _, tail = raw.partition(":")
        path_part = head
        try:
            rel = _relativise(path_part, sandbox_root)
        except ValueError:
            rel = path_part
        matches.append(f"{rel}:{tail}")
        if len(matches) >= max_matches:
            truncated = True
            break
    return _format_result(matches, truncated, max_matches)


def _relativise(path_str: str, root: Path) -> str:
    from pathlib import Path as _P

    p = _P(path_str)
    return str(p.relative_to(root))


def _format_result(matches: list[str], truncated: bool, max_matches: int) -> str:
    if not matches:
        return "[no matches]"
    body = "\n".join(matches)
    if truncated:
        body += f"\n... [truncated: match cap {max_matches} reached]"
    return body


def make_grep(
    sandbox: WorkspaceSandbox,
    *,
    max_matches: int = DEFAULT_MAX_MATCHES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Tool:
    """Build a ``grep`` tool bound to ``sandbox``.

    Args:
        sandbox: The workspace sandbox whose root confines path resolution.
        max_matches: Cap on total matches returned. Defaults to 200.
        max_file_bytes: Files larger than this are skipped to keep the
            search bounded on large trees.
    """

    @tool(
        name="grep",
        description=(
            "Search for a regex pattern under a path in the workspace. "
            "Returns up to "
            f"{max_matches} matches as 'path:lineno:line'. Binary files and "
            "files larger than "
            f"{max_file_bytes} bytes are skipped. Uses ripgrep when "
            "available, otherwise a pure-Python walker."
        ),
        risk_tier=1,
        side_effects="read",
        timeout_s=30.0,
        args_model=GrepArgs,
    )
    async def grep(args: GrepArgs, ctx: TurnContext) -> str:  # noqa: ARG001
        search_root = sandbox.resolve(args.path)
        if not search_root.exists():
            raise ToolError(f"Search path does not exist: {args.path}")

        rg = _ripgrep_path()
        if rg is not None:
            return await _ripgrep_search(
                rg_path=rg,
                search_root=search_root,
                sandbox_root=sandbox.root,
                pattern=args.pattern,
                glob=args.glob,
                max_matches=max_matches,
                max_file_bytes=max_file_bytes,
            )
        return await _python_search(
            search_root=search_root,
            sandbox_root=sandbox.root,
            pattern=args.pattern,
            glob=args.glob,
            max_matches=max_matches,
            max_file_bytes=max_file_bytes,
        )

    return grep


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_MATCHES",
    "GrepArgs",
    "make_grep",
]
