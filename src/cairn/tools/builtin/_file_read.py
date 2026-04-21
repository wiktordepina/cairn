"""`file_read` — Tier 1, sandboxed text read.

Paths resolve through a `WorkspaceSandbox` — absolute paths, `..`
escapes, and symlink escapes are rejected before any I/O. Binary files
return a one-line summary rather than their contents; oversized files
are truncated with a suffix carrying the original byte count.

The tool is constructed via `make_file_read(sandbox)` so the CLI can
wire the same tool against whichever workspace root is in scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from cairn.tools._decorator import tool
from cairn.tools._errors import ToolError

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._protocols import Tool
    from cairn.tools.security._sandbox import WorkspaceSandbox


DEFAULT_MAX_BYTES = 1_000_000
_BINARY_PROBE_BYTES = 8_192


class FileReadArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace root.")


def _binary_summary(path: Path, data: bytes, file_size: int) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None:
        mime = "application/octet-stream"
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f"[binary: {mime}, {file_size} bytes, sha256:{digest}]"


def make_file_read(
    sandbox: WorkspaceSandbox,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Tool:
    """Build a `file_read` tool bound to `sandbox`.

    Args:
        sandbox: The workspace sandbox whose root confines path resolution.
        max_bytes: Text content over this size is truncated. Defaults to
            `DEFAULT_MAX_BYTES` (1 MB).
    """

    @tool(
        name="file_read",
        description=(
            "Read a text file from the workspace. Paths are relative to the "
            "workspace root; absolute paths and escape attempts are rejected. "
            "Binary files return a one-line summary rather than their "
            f"contents. Files larger than {max_bytes} bytes are truncated."
        ),
        risk_tier=1,
        side_effects="read",
        timeout_s=10.0,
        args_model=FileReadArgs,
    )
    async def file_read(args: FileReadArgs, ctx: TurnContext) -> str:  # noqa: ARG001
        target = sandbox.resolve(args.path)

        def _read_sync() -> str:
            if not target.exists():
                raise ToolError(f"File not found: {args.path}")
            if not target.is_file():
                raise ToolError(f"Not a regular file: {args.path}")
            sandbox.assert_readable(target)

            file_size = target.stat().st_size
            with target.open("rb") as f:
                data = f.read(max_bytes + 1)

            head = data[:_BINARY_PROBE_BYTES]
            if b"\x00" in head:
                return _binary_summary(target, data, file_size)

            if len(data) > max_bytes:
                truncated = data[:max_bytes]
                text = truncated.decode("utf-8", errors="ignore")
                return f"{text}\n... [truncated: {file_size} bytes total]"

            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return _binary_summary(target, data, file_size)

        return await asyncio.to_thread(_read_sync)

    return file_read


__all__ = ["DEFAULT_MAX_BYTES", "FileReadArgs", "make_file_read"]
