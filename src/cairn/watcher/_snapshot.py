"""Watch-set snapshot — pure functions over the file system.

`build_watch_set` consumes `(category, path)` rows and returns a
frozen `WatchSet` of `WatchEntry` rows that record each path's
mtime, size, and SHA-256 at snapshot time. `snapshot_path` is the
single-path helper. `discover_watch_paths` is the runtime glue that
walks the live config / convention / profile-doc objects to produce
the rows `build_watch_set` consumes.

Files that don't exist or aren't readable at snapshot time are
omitted entirely — the watcher only watches paths that were
present on boot. Paths created afterwards are invisible until
`/reload` re-snapshots.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from cairn.config import ConventionFilesConfig
    from cairn.conventions import ConventionLoader
    from cairn.memory import ProfileDocLoader


log = logging.getLogger(__name__)


WatchCategory = Literal["config", "convention", "profile_doc"]
"""Source bucket for a watched file. Drives banner copy + log fields."""


_HASH_CHUNK_SIZE = 64 * 1024
"""Bytes per read when streaming a file through SHA-256. Hard-coded;
see design doc §8 — watched files are small so a tunable would be
noise."""


@dataclass(frozen=True, slots=True)
class WatchEntry:
    """One file's snapshotted state at boot (or after `/reload`)."""

    category: WatchCategory
    path: Path
    mtime_ns: int
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class WatchSet:
    """Immutable bundle of all `WatchEntry` rows the watcher tracks."""

    entries: tuple[WatchEntry, ...]

    def by_path(self) -> dict[Path, WatchEntry]:
        """Return the entries indexed by `path` for fast lookup."""
        return {entry.path: entry for entry in self.entries}


def snapshot_path(category: WatchCategory, path: Path) -> WatchEntry | None:
    """Return a `WatchEntry` for *path*, or `None` if unreadable.

    Returns `None` when the file is missing, is a directory, or read
    fails for any reason. Logs a DEBUG record for the missing /
    unreadable cases — these are quietly skipped at boot.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        log.debug("watch snapshot: %s does not exist", path)
        return None
    except OSError:
        log.debug("watch snapshot: stat failed for %s", path, exc_info=True)
        return None

    if not path.is_file():
        log.debug("watch snapshot: %s is not a regular file", path)
        return None

    digest = _sha256_of(path)
    if digest is None:
        return None

    return WatchEntry(
        category=category,
        path=path,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        sha256=digest,
    )


def build_watch_set(entries: Iterable[tuple[WatchCategory, Path]]) -> WatchSet:
    """Snapshot each `(category, path)` row, dropping unreadable ones.

    Duplicates (same `path` under different categories) are de-duped on
    first match — order matters: pass categories in priority order if
    that distinction ever surfaces. Today no path appears in two
    categories.
    """
    seen: set[Path] = set()
    out: list[WatchEntry] = []
    for category, path in entries:
        resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        snap = snapshot_path(category, resolved)
        if snap is not None:
            out.append(snap)
    return WatchSet(entries=tuple(out))


def discover_watch_paths(
    *,
    convention_files_config: ConventionFilesConfig,
    convention_loader: ConventionLoader,
    profile_doc_loader: ProfileDocLoader,
    project_dir: Path | None = None,
) -> list[tuple[WatchCategory, Path]]:
    """Enumerate every path the watcher should track at this snapshot.

    Pulls from three sources:

    - **config layers** — `config_paths(project_dir=...)` filtered to
      paths that currently exist on disk.
    - **convention files** — `discover_user_files` and
      `discover_project_files` results, both deduped on resolved path.
    - **profile docs** — the three paths the `ProfileDocLoader` reads
      (soul / user-context / MEMORY.md), filtered to existing files.

    Returned in stable order: config first, then conventions, then
    profile docs. Within each group, the underlying discovery
    function's order is preserved.
    """
    # Imports here keep the watcher's import graph lean.
    from cairn.config import config_paths
    from cairn.conventions import discover_project_files, discover_user_files

    out: list[tuple[WatchCategory, Path]] = []

    for _name, layer_path in config_paths(project_dir=project_dir):
        if layer_path.is_file():
            out.append(("config", layer_path))

    if convention_files_config.enabled:
        for path, _filename, _source in discover_user_files(
            convention_files_config.user_level_paths,
        ):
            out.append(("convention", path))
        for path, _filename, _source in discover_project_files(
            convention_loader.cwd,
            convention_files_config,
        ):
            out.append(("convention", path))

    for doc_path in (
        profile_doc_loader.soul_path,
        profile_doc_loader.user_context_path,
        profile_doc_loader.memory_md_path,
    ):
        if doc_path.is_file():
            out.append(("profile_doc", doc_path))

    return out


def _sha256_of(path: Path) -> str | None:
    """Stream *path* through SHA-256 in 64 KiB chunks; `None` on read error."""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        log.debug("watch snapshot: hash failed for %s", path, exc_info=True)
        return None
    return hasher.hexdigest()
