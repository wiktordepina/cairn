"""`file_write` — Tier 3, sandboxed text write.

Paths resolve through the same `WorkspaceSandbox` as `file_read`.
Three modes:

- `"create"` — fails if the target already exists.
- `"overwrite"` — replaces the existing file (or creates it).
- `"append"` — extends the existing file (or creates it).

Parent directories are not auto-created — callers pass paths that
terminate inside an existing directory. The tool writes UTF-8. Size of
the pending write is capped to mirror `file_read`'s read cap.

Approval is required by default (Tier 3); the session allowlist grants
"first-run then subsequent writes to the same path" via the runner's
approval chain (same args-signature).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from cairn.tools._decorator import tool
from cairn.tools._errors import ToolError

if TYPE_CHECKING:
    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._protocols import Tool
    from cairn.tools.security._sandbox import WorkspaceSandbox


DEFAULT_MAX_BYTES = 1_000_000


class FileWriteArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace root.")
    content: str = Field(description="UTF-8 text to write.")
    mode: Literal["overwrite", "append", "create"] = Field(
        default="overwrite",
        description=(
            "Write mode. 'create' fails if the path exists; 'overwrite' "
            "replaces it; 'append' extends it. All modes require the parent "
            "directory to exist."
        ),
    )


def make_file_write(
    sandbox: WorkspaceSandbox,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Tool:
    """Build a `file_write` tool bound to `sandbox`.

    Args:
        sandbox: The workspace sandbox whose root confines path resolution.
        max_bytes: Writes larger than this are refused. Matches
            `file_read`'s read cap by default.
    """

    @tool(
        name="file_write",
        description=(
            "Write UTF-8 text to a file inside the workspace. Modes: "
            "'create' (fail if exists), 'overwrite' (default), 'append'. "
            "Paths are relative to the workspace root; parent directories "
            f"must already exist. Writes larger than {max_bytes} bytes are "
            "rejected."
        ),
        risk_tier=3,
        side_effects="write",
        timeout_s=10.0,
        args_model=FileWriteArgs,
    )
    async def file_write(args: FileWriteArgs, ctx: TurnContext) -> str:  # noqa: ARG001
        target = sandbox.resolve(args.path)
        payload = args.content.encode("utf-8")
        if len(payload) > max_bytes:
            raise ToolError(f"Write of {len(payload)} bytes exceeds max_bytes={max_bytes}")

        def _write_sync() -> str:
            sandbox.assert_writable(target)
            parent = target.parent
            if not parent.exists():
                raise ToolError(
                    f"Parent directory does not exist: {parent.relative_to(sandbox.root)}"
                )
            if not parent.is_dir():
                raise ToolError(f"Parent is not a directory: {parent}")

            if args.mode == "create":
                if target.exists():
                    raise ToolError(f"File already exists (mode=create): {args.path}")
                target.write_bytes(payload)
            elif args.mode == "overwrite":
                if target.exists() and not target.is_file():
                    raise ToolError(f"Refusing to overwrite non-regular file: {args.path}")
                target.write_bytes(payload)
            elif args.mode == "append":
                if target.exists() and not target.is_file():
                    raise ToolError(f"Refusing to append to non-regular file: {args.path}")
                with target.open("ab") as f:
                    f.write(payload)

            return f"Wrote {len(payload)} bytes to {args.path} (mode={args.mode})"

        return await asyncio.to_thread(_write_sync)

    return file_write


__all__ = ["DEFAULT_MAX_BYTES", "FileWriteArgs", "make_file_write"]
