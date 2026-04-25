"""Tests for `cairn trust add/list/remove`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from cairn.cli import app
from cairn.conventions._trust import AllowlistStore, default_allowlist_path

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# `cairn trust add`
# ---------------------------------------------------------------------------


class TestTrustAdd:
    def test_cwd_default(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["trust", "add"])
        assert result.exit_code == 0, result.output
        assert "✓ Added" in result.output

        store = AllowlistStore(default_allowlist_path())
        entries = store.list_trusted()
        assert len(entries) == 1
        # cwd was monkey-patched to <tmp>/project by `isolated_home`.
        assert entries[0].path == (isolated_home / "project").resolve()

    def test_explicit_path(self, isolated_home: Path) -> None:
        target = isolated_home / "project" / "subdir"
        target.mkdir()
        result = CliRunner().invoke(app, ["trust", "add", str(target)])
        assert result.exit_code == 0, result.output

        store = AllowlistStore(default_allowlist_path())
        entries = store.list_trusted()
        assert len(entries) == 1
        assert entries[0].path == target.resolve()

    def test_idempotent_re_add(self, isolated_home: Path) -> None:
        # First add.
        first = CliRunner().invoke(app, ["trust", "add"])
        assert first.exit_code == 0
        assert "✓ Added" in first.output

        # Second add of the same path.
        second = CliRunner().invoke(app, ["trust", "add"])
        assert second.exit_code == 0
        assert "already trusted" in second.output

        # Still only one entry.
        store = AllowlistStore(default_allowlist_path())
        assert len(store.list_trusted()) == 1


# ---------------------------------------------------------------------------
# `cairn trust list`
# ---------------------------------------------------------------------------


class TestTrustList:
    def test_empty_store(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["trust", "list"])
        assert result.exit_code == 0, result.output
        assert "(no trusted projects)" in result.output

    def test_multi_entry_insertion_order(self, isolated_home: Path) -> None:
        # Add three projects in deliberate order.
        first = isolated_home / "project"
        second = isolated_home / "second"
        third = isolated_home / "third"
        second.mkdir()
        third.mkdir()
        store = AllowlistStore(default_allowlist_path())
        for path in (first, second, third):
            store.add(path)

        result = CliRunner().invoke(app, ["trust", "list"])
        assert result.exit_code == 0, result.output
        out_lines = [
            line for line in result.output.splitlines() if line and not line.startswith("(")
        ]
        # AllowlistStore preserves insertion order.
        assert str(first.resolve()) in out_lines[0]
        assert str(second.resolve()) in out_lines[1]
        assert str(third.resolve()) in out_lines[2]


# ---------------------------------------------------------------------------
# `cairn trust remove`
# ---------------------------------------------------------------------------


class TestTrustRemove:
    def test_remove_existing(self, isolated_home: Path) -> None:
        store = AllowlistStore(default_allowlist_path())
        store.add(isolated_home / "project")

        result = CliRunner().invoke(app, ["trust", "remove"])
        assert result.exit_code == 0, result.output
        assert "✓ Removed" in result.output
        assert store.list_trusted() == []

    def test_remove_explicit_path(self, isolated_home: Path) -> None:
        other = isolated_home / "elsewhere"
        other.mkdir()
        store = AllowlistStore(default_allowlist_path())
        store.add(other)

        result = CliRunner().invoke(app, ["trust", "remove", str(other)])
        assert result.exit_code == 0, result.output
        assert store.list_trusted() == []

    def test_remove_unknown_exits_one(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["trust", "remove"])
        assert result.exit_code == 1
        assert "not in allowlist" in result.stderr
