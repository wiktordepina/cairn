"""Tests for `cairn.watcher._snapshot` — pure snapshot logic."""

from __future__ import annotations

import dataclasses
import hashlib
from typing import TYPE_CHECKING

import pytest

from cairn.watcher import (
    WatchEntry,
    WatchSet,
    build_watch_set,
    snapshot_path,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# snapshot_path
# ---------------------------------------------------------------------------


class TestSnapshotPath:
    def test_existing_file_round_trips(self, tmp_path: Path) -> None:
        body = b"hello watcher\n"
        target = tmp_path / "config.toml"
        target.write_bytes(body)

        snap = snapshot_path("config", target)

        assert snap is not None
        assert snap.category == "config"
        assert snap.path == target
        assert snap.size == len(body)
        assert snap.sha256 == hashlib.sha256(body).hexdigest()
        assert snap.mtime_ns == target.stat().st_mtime_ns

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert snapshot_path("config", tmp_path / "nope.toml") is None

    def test_directory_returns_none(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert snapshot_path("config", sub) is None

    def test_empty_file_hashes(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_bytes(b"")
        snap = snapshot_path("convention", empty)
        assert snap is not None
        assert snap.size == 0
        assert snap.sha256 == hashlib.sha256(b"").hexdigest()

    def test_large_file_hashes_correctly(self, tmp_path: Path) -> None:
        # Larger than the 64 KiB chunk size to exercise the loop.
        body = b"a" * (200_000)
        target = tmp_path / "big.md"
        target.write_bytes(body)
        snap = snapshot_path("profile_doc", target)
        assert snap is not None
        assert snap.sha256 == hashlib.sha256(body).hexdigest()
        assert snap.size == len(body)

    def test_entry_is_frozen(self, tmp_path: Path) -> None:
        target = tmp_path / "x.toml"
        target.write_bytes(b"x")
        snap = snapshot_path("config", target)
        assert snap is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.size = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_watch_set
# ---------------------------------------------------------------------------


class TestBuildWatchSet:
    def test_collects_existing_files(self, tmp_path: Path) -> None:
        a = tmp_path / "a.toml"
        b = tmp_path / "b.md"
        c = tmp_path / "c.md"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        c.write_bytes(b"c")

        result = build_watch_set(
            [
                ("config", a),
                ("convention", b),
                ("profile_doc", c),
            ],
        )

        assert isinstance(result, WatchSet)
        assert len(result.entries) == 3
        assert {e.category for e in result.entries} == {
            "config",
            "convention",
            "profile_doc",
        }

    def test_drops_missing_files(self, tmp_path: Path) -> None:
        present = tmp_path / "p.toml"
        present.write_bytes(b"x")
        missing = tmp_path / "missing.toml"

        result = build_watch_set(
            [
                ("config", present),
                ("config", missing),
            ],
        )

        assert len(result.entries) == 1
        assert result.entries[0].path == present

    def test_dedup_on_path(self, tmp_path: Path) -> None:
        target = tmp_path / "shared.md"
        target.write_bytes(b"shared")

        result = build_watch_set(
            [
                ("convention", target),
                ("profile_doc", target),
            ],
        )

        assert len(result.entries) == 1
        assert result.entries[0].category == "convention"  # first wins

    def test_empty_input_yields_empty_set(self) -> None:
        result = build_watch_set([])
        assert isinstance(result, WatchSet)
        assert result.entries == ()

    def test_by_path_index(self, tmp_path: Path) -> None:
        a = tmp_path / "a.toml"
        b = tmp_path / "b.md"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        result = build_watch_set([("config", a), ("convention", b)])

        idx = result.by_path()
        assert set(idx.keys()) == {a, b}
        assert idx[a].category == "config"
        assert idx[b].category == "convention"

    def test_set_is_frozen(self, tmp_path: Path) -> None:
        result = build_watch_set([])
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.entries = (
                WatchEntry(category="config", path=tmp_path, mtime_ns=0, size=0, sha256=""),
            )  # type: ignore[misc]
