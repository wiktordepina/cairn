"""Project convention-file loading.

Reads Markdown convention files (`CAIRN.md`, `AGENTS.md`, `CLAUDE.md`
by default) from the user's project + optional user-level fallbacks,
wraps each in a `<project_conventions>` envelope, and exposes them for
injection into the system prompt by the context manager. See
`.plan/conventions-brick-design.md`.
"""

from __future__ import annotations

from cairn.conventions._discovery import (
    DEFAULT_SKIP_DIRS,
    ConventionSource,
    discover_project_files,
    discover_user_files,
    find_git_root,
)

__all__ = [
    "DEFAULT_SKIP_DIRS",
    "ConventionSource",
    "discover_project_files",
    "discover_user_files",
    "find_git_root",
]
