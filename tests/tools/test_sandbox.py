"""Tests for WorkspaceSandbox."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.tools.security import PathEscape, WorkspaceSandbox

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sandbox(tmp_path: Path) -> WorkspaceSandbox:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# readme\n")
    return WorkspaceSandbox(root=tmp_path)


class TestResolve:
    def test_simple_relative(self, sandbox: WorkspaceSandbox) -> None:
        p = sandbox.resolve("src/main.py")
        assert p.exists()
        assert p.read_text() == "print('hi')\n"

    def test_nested_relative(self, sandbox: WorkspaceSandbox) -> None:
        p = sandbox.resolve("docs/readme.md")
        assert p.name == "readme.md"

    def test_rejects_absolute(self, sandbox: WorkspaceSandbox) -> None:
        with pytest.raises(PathEscape, match="Absolute"):
            sandbox.resolve("/etc/passwd")

    def test_rejects_empty(self, sandbox: WorkspaceSandbox) -> None:
        with pytest.raises(PathEscape, match="Empty"):
            sandbox.resolve("")

    def test_rejects_dotdot_escape(self, sandbox: WorkspaceSandbox) -> None:
        with pytest.raises(PathEscape, match="outside"):
            sandbox.resolve("../../etc/passwd")

    def test_rejects_dotdot_with_reentry(self, sandbox: WorkspaceSandbox) -> None:
        # src/../../outside → escapes
        with pytest.raises(PathEscape):
            sandbox.resolve("src/../../elsewhere")

    def test_allows_dotdot_when_stays_inside(self, sandbox: WorkspaceSandbox) -> None:
        # src/../docs/readme.md → inside
        p = sandbox.resolve("src/../docs/readme.md")
        assert p.read_text().startswith("# readme")

    def test_non_existent_path_allowed(self, sandbox: WorkspaceSandbox) -> None:
        # resolve() canonicalises without strict=True, so non-existent
        # paths inside the sandbox resolve cleanly — callers enforce
        # existence at the file-op level.
        p = sandbox.resolve("src/new_file.py")
        assert p.name == "new_file.py"

    def test_rejects_symlink_escape(self, tmp_path: Path, sandbox: WorkspaceSandbox) -> None:
        # Create a symlink inside the sandbox that points outside.
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "src" / "escape_link"
        link.symlink_to(outside)
        with pytest.raises(PathEscape):
            sandbox.resolve("src/escape_link/secret")


class TestAsserts:
    def test_readable_inside_ok(self, sandbox: WorkspaceSandbox) -> None:
        p = sandbox.resolve("src/main.py")
        sandbox.assert_readable(p)  # does not raise

    def test_readable_outside_raises(self, tmp_path: Path, sandbox: WorkspaceSandbox) -> None:
        outside = tmp_path.parent / "other.txt"
        with pytest.raises(PathEscape):
            sandbox.assert_readable(outside)

    def test_writable_inside_ok(self, sandbox: WorkspaceSandbox) -> None:
        p = sandbox.resolve("new.txt")
        sandbox.assert_writable(p)

    def test_writable_outside_raises(self, tmp_path: Path, sandbox: WorkspaceSandbox) -> None:
        with pytest.raises(PathEscape):
            sandbox.assert_writable(tmp_path.parent / "xx")


class TestConstruction:
    def test_strict_resolve(self, tmp_path: Path) -> None:
        # Constructor requires the root to exist.
        missing = tmp_path / "nope"
        with pytest.raises(FileNotFoundError):
            WorkspaceSandbox(root=missing)

    def test_root_property(self, sandbox: WorkspaceSandbox, tmp_path: Path) -> None:
        assert sandbox.root == tmp_path.resolve()
