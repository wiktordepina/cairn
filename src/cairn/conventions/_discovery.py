"""Discovery — finds convention files on disk.

Two entry points:

- `discover_project_files` walks up from cwd (to a boundary governed
  by `walk_up_to`), taking the **nearest** match per filename. If
  `search_subdirs` is true, additional files are also emitted from
  nested directories under cwd, ordered shallower-first.
- `discover_user_files` opens each path in `user_level_paths`
  verbatim. These are user-owned fallbacks (e.g.
  `$XDG_CONFIG_HOME/cairn/CAIRN.md`) and bypass the trust gate.

Both return lists of `(path, filename, source)` tuples in the order
they should appear in the assembled system prompt. Broader → more
specific: user-level first, then ancestor-project, then nested.

Deduplication is by **resolved path** — a file that matches both an
ancestor walk and a nested descent is emitted once.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cairn.config import ConventionFilesConfig


log = logging.getLogger(__name__)


DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
    },
)
"""Directories skipped by nested descent. Convention files don't live
inside build artefacts or dependency trees."""


class ConventionSource(StrEnum):
    """Where a convention file came from."""

    USER = "user"
    PROJECT = "project"


def find_git_root(start: Path) -> Path | None:
    """Return the directory containing `.git` (dir or file) at or above
    *start*, or `None` if not inside a git repo.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        git = candidate / ".git"
        if git.exists():
            return candidate
    return None


def discover_project_files(
    cwd: Path,
    config: ConventionFilesConfig,
) -> list[tuple[Path, str, ConventionSource]]:
    """Discover project-level convention files.

    Returns tuples in emission order:
      1. Nearest-ancestor match per filename (in `config.filenames`
         order).
      2. If `search_subdirs=True`, nested descents — shallower first.

    Results are deduped on resolved path — a file matched twice (once
    as ancestor, once as nested) is emitted only on its first match.
    """
    if not config.enabled or not config.filenames:
        return []

    cwd = cwd.resolve()
    boundary = _resolve_boundary(cwd, config.walk_up_to)

    out: list[tuple[Path, str, ConventionSource]] = []
    seen: set[Path] = set()

    for filename in config.filenames:
        # 1. Ancestor walk — nearest wins.
        ancestor_hit: Path | None = None
        if config.walk_up_to != "cwd_only":
            for directory in _ancestors_up_to(cwd, boundary):
                candidate = directory / filename
                if _is_readable_file(candidate):
                    ancestor_hit = candidate.resolve()
                    break
        else:
            candidate = cwd / filename
            if _is_readable_file(candidate):
                ancestor_hit = candidate.resolve()

        if ancestor_hit is not None and ancestor_hit not in seen:
            seen.add(ancestor_hit)
            out.append((ancestor_hit, filename, ConventionSource.PROJECT))

        # 2. Nested descent — shallower first, skipping re-hits.
        if config.search_subdirs:
            for nested in _iter_nested(
                cwd,
                filename,
                max_depth=config.max_nested_depth,
            ):
                resolved = nested.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append((resolved, filename, ConventionSource.PROJECT))

    return out


def discover_user_files(
    user_level_paths: list[str],
) -> list[tuple[Path, str, ConventionSource]]:
    """Open each path in *user_level_paths* and return the readable ones.

    Path entries support `~` and `$VAR` expansion. Missing files are
    skipped silently. Non-file targets (directories, broken symlinks)
    log a WARNING and skip.

    The trust gate does not apply to these — they're under the user's
    own config dir (same trust boundary as soul document / user-context
    / MEMORY.md).
    """
    out: list[tuple[Path, str, ConventionSource]] = []
    seen: set[Path] = set()
    for raw in user_level_paths:
        expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not expanded.exists():
            continue
        if not expanded.is_file():
            log.warning(
                "convention user_level_paths entry is not a regular file; skipping: %s",
                expanded,
            )
            continue
        resolved = expanded.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((resolved, expanded.name, ConventionSource.USER))
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_boundary(
    cwd: Path,
    walk_up_to: str,
) -> Path:
    """Return the (inclusive) top-most directory the ancestor walk may visit."""
    if walk_up_to == "cwd_only":
        return cwd
    if walk_up_to == "filesystem_root":
        return Path(cwd.anchor or "/")
    # "git_root" — fall back to cwd_only when not in a repo.
    root = find_git_root(cwd)
    if root is None:
        log.debug("no git root found from %s; falling back to cwd_only", cwd)
        return cwd
    return root


def _ancestors_up_to(cwd: Path, boundary: Path) -> Iterable[Path]:
    """Yield cwd, parent, grandparent, … stopping inclusive at *boundary*."""
    current = cwd
    while True:
        yield current
        if current == boundary:
            return
        parent = current.parent
        if parent == current:
            # Filesystem root; stop defensively even if boundary mismatches.
            return
        current = parent


def _iter_nested(
    cwd: Path,
    filename: str,
    *,
    max_depth: int,
) -> Iterable[Path]:
    """Yield matches for *filename* at depths 1..`max_depth` under *cwd*.

    Shallower directories are visited first. Skips directories in
    `DEFAULT_SKIP_DIRS`, any hidden directory (`.` prefix) at depth > 0,
    and any symlink.
    """
    if max_depth < 1:
        return
    # Breadth-first walk so shallower files come out first.
    frontier: list[tuple[Path, int]] = [(cwd, 0)]
    while frontier:
        next_frontier: list[tuple[Path, int]] = []
        for directory, depth in frontier:
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            # Emit files at this depth (depth>0 since cwd/filename is the
            # ancestor walk's job).
            if depth > 0:
                target = directory / filename
                if _is_readable_file(target):
                    yield target
            if depth >= max_depth:
                continue
            for entry in entries:
                if not _should_descend(entry):
                    continue
                next_frontier.append((entry, depth + 1))
        frontier = next_frontier


def _should_descend(entry: Path) -> bool:
    """True if *entry* is a directory we're willing to descend into."""
    try:
        if entry.is_symlink():
            return False
        if not entry.is_dir():
            return False
    except OSError:
        return False
    name = entry.name
    if name in DEFAULT_SKIP_DIRS:
        return False
    # Don't descend into hidden dirs below cwd.
    return not name.startswith(".")


def _is_readable_file(path: Path) -> bool:
    """True if *path* exists and is a regular file (not a dir or symlink-
    to-dir). Symlinks to files are allowed.
    """
    try:
        return path.is_file()
    except OSError:
        return False
