"""Workspace filesystem sandbox.

Every built-in filesystem tool resolves caller-supplied paths through
`WorkspaceSandbox.resolve`. The sandbox:

- Rejects absolute paths.
- Rejects paths that, once canonicalised, escape the workspace root.
- Rejects paths whose resolution follows a symlink out of the workspace.

`PathEscape` (`ToolError` subtype) surfaces on violation. The tool
catches + returns an error `ToolResultBlock`; no exception reaches
the orchestrator's unexpected-error path.
"""

from __future__ import annotations

from pathlib import Path

from cairn.tools._errors import PathEscape


class WorkspaceSandbox:
    """Resolves caller-supplied paths into safe absolute paths rooted
    under `root`.

    `root` itself must exist at construction time (`strict=True`).
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str) -> Path:
        """Canonicalise `path` (treated as relative to the workspace
        root) and return the absolute Path.

        Raises:
            PathEscape: path is absolute, contains `..` that escapes
                the sandbox, or resolves through a symlink that points
                outside the sandbox.
        """
        if not path:
            raise PathEscape("Empty path is not allowed.")

        p = Path(path)
        if p.is_absolute():
            raise PathEscape(f"Absolute paths are not allowed inside the sandbox: {path!r}")

        candidate = (self._root / p).resolve(strict=False)

        # is_relative_to added in 3.9; use relative_to + try/except for
        # more precise control.
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise PathEscape(
                f"Path {path!r} resolves to {candidate}, which is outside "
                f"the sandbox root {self._root}."
            ) from exc

        return candidate

    def assert_readable(self, path: Path) -> None:
        """Ensure `path` lies inside the sandbox. Call with paths
        obtained from `resolve` — belt-and-braces against callers
        constructing paths by other means."""
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise PathEscape(f"{path} is outside the sandbox root {self._root}.") from exc

    def assert_writable(self, path: Path) -> None:
        """Same as `assert_readable` — V1 has no read/write distinction
        beyond the sandbox membership check. Kept as a separate method
        so future policies (e.g. specific dirs are read-only) can land
        without changing call sites."""
        self.assert_readable(path)
