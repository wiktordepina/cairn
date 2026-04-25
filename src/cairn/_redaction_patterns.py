"""Shared API-key redaction patterns.

Single source of truth for the regex set used by both the tool-output
redactor (`cairn.tools.SecretRedactor`) and the log-record redactor
(`cairn.logging.RedactingFilter`). Keeping them in one module prevents
the two implementations from drifting as new key shapes are added.

Each pattern captures an optional prefix (group 1) that is preserved
in the substitution; the rest is replaced with `[REDACTED]`. Patterns
without a meaningful prefix use an empty group so the same
substitution string works uniformly.
"""

from __future__ import annotations

import re

# Patterns from security doc §4.
REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{20,}"),
    re.compile(r"()(sk-[A-Za-z0-9]{20,})"),  # OpenAI-shaped
    re.compile(r"()(sk-ant-[A-Za-z0-9_\-]{20,})"),  # Anthropic-shaped
    re.compile(r"()(AIza[0-9A-Za-z_\-]{35})"),  # Google API
    re.compile(r"()(AKIA[0-9A-Z]{16})"),  # AWS access key
)


def redact_text(text: str) -> str:
    """Replace any API-key-shaped strings with `[REDACTED]` while
    preserving structural context (e.g. the `Bearer ` prefix)."""
    for pattern in REDACTION_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text
