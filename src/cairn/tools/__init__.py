"""Cairn tool system — concrete Tool implementations, registry, runner.

Replaces the stubs the orchestrator ships with (RaisingToolRunner,
EmptyToolRegistry). Real ApprovalGateway still lives in the UI brick.
"""

from cairn.tools._decorator import tool
from cairn.tools._errors import (
    PathEscape,
    SSRFBlocked,
    ToolError,
    ToolRetry,
    ToolTimeout,
)
from cairn.tools._registry import DefaultToolRegistry

__all__ = [
    "DefaultToolRegistry",
    "PathEscape",
    "SSRFBlocked",
    "ToolError",
    "ToolRetry",
    "ToolTimeout",
    "tool",
]
