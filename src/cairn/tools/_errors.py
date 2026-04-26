"""Tool-system error hierarchy.

Distinct shapes for distinct runner responses:

- `ToolError` — expected, user-meaningful failure. Runner returns
  `ToolResultBlock(is_error=True, ...)` with `error_class="user"`
  and surfaces the message to the model.
- `ToolTimeout` — raised internally when `asyncio.timeout` fires.
  Runner marks the call `timed_out`.
- `PathEscape` — filesystem tool attempted to escape the workspace
  sandbox. A specific `ToolError`.
- `SSRFBlocked` — URL-fetching tool hit a disallowed IP / scheme /
  redirect. A specific `ToolError`.
"""

from __future__ import annotations


class ToolError(Exception):
    """Expected, user-meaningful failure inside a tool. Surfaces to the
    model as an error ToolResultBlock."""


class ToolTimeout(Exception):
    """Raised by the runner when `asyncio.timeout` fires around
    `tool.invoke`."""


class PathEscape(ToolError):
    """A path handed to a filesystem tool would escape the workspace
    sandbox. Raised by `WorkspaceSandbox.resolve`."""


class SSRFBlocked(ToolError):
    """A URL-fetching tool was asked to reach a disallowed host. Raised
    by the SSRF defence module."""
