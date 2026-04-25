"""Cairn file watcher — drift detection for config / convention / profile docs.

The watcher snapshots a fixed set of paths on boot
(`build_watch_set`) and polls them on a configurable interval
(`FileWatcher`). When a watched file's content changes, it emits
`ConfigDriftDetected` once per drift transition; the running app's
`/reload` command consumes the signal and applies the new config
surgically.

Design: `.plan/file-watcher-brick-design.md`.
"""

from __future__ import annotations

from cairn.watcher._poller import FileWatcher
from cairn.watcher._snapshot import (
    WatchCategory,
    WatchEntry,
    WatchSet,
    build_watch_set,
    discover_watch_paths,
    snapshot_path,
)

__all__ = [
    "FileWatcher",
    "WatchCategory",
    "WatchEntry",
    "WatchSet",
    "build_watch_set",
    "discover_watch_paths",
    "snapshot_path",
]
