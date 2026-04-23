"""Render `ConventionFile` instances as `<project_conventions>` envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.conventions._discovery import ConventionSource


@dataclass(frozen=True, slots=True)
class ConventionFile:
    """One loaded convention file, ready for the system prompt."""

    filename: str
    """Basename like `AGENTS.md` — placed in the envelope's
    `source="..."` attribute."""

    path: Path
    """Absolute, resolved path to the file."""

    content: str
    """UTF-8 text, already byte-capped + paragraph-truncated. If
    `truncated_from` is set, `content` ends with the truncation
    marker comment."""

    truncated_from: int | None
    """Original byte length if truncation occurred; `None` otherwise."""

    source: ConventionSource
    """Where the file came from (user-level fallback vs project)."""


def wrap_one(f: ConventionFile) -> str:
    """Return one `<project_conventions>` envelope for *f*."""
    return (
        f'<project_conventions source="{f.filename}" path="{f.path}">\n'
        f"{f.content}\n"
        f"</project_conventions>"
    )


def render_conventions(files: list[ConventionFile]) -> str:
    """Render a list of files as stacked envelopes separated by `\\n\\n`.

    Returns `""` for an empty list so callers can treat the result
    uniformly with the other `_wrap_section` outputs.
    """
    if not files:
        return ""
    return "\n\n".join(wrap_one(f) for f in files)
