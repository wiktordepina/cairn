"""Cairn tool system — concrete Tool implementations, registry, runner.

Replaces the stubs the orchestrator ships with (RaisingToolRunner,
EmptyToolRegistry). Real ApprovalGateway still lives in the UI brick.
"""

from cairn.tools._approvers import (
    AutoApproveReadOnly,
    SessionAllowlist,
    TierGate,
)
from cairn.tools._decorator import tool
from cairn.tools._errors import (
    PathEscape,
    SSRFBlocked,
    ToolError,
    ToolRetry,
    ToolTimeout,
)
from cairn.tools._registry import DefaultToolRegistry
from cairn.tools._transformers import (
    InvisibleUnicodeStripper,
    SecretRedactor,
    SpotlightTransformer,
    redact_secrets,
    strip_invisible_unicode,
)

__all__ = [
    # Approvers
    "AutoApproveReadOnly",
    "SessionAllowlist",
    "TierGate",
    # Errors
    "PathEscape",
    "SSRFBlocked",
    "ToolError",
    "ToolRetry",
    "ToolTimeout",
    # Registry + decorator
    "DefaultToolRegistry",
    "tool",
    # Transformers
    "InvisibleUnicodeStripper",
    "SecretRedactor",
    "SpotlightTransformer",
    "redact_secrets",
    "strip_invisible_unicode",
]
