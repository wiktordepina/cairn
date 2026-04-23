"""Tests for convention-file discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.config import ConventionFilesConfig
from cairn.conventions import (
    ConventionSource,
    discover_project_files,
    discover_user_files,
    find_git_root,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal project rooted at *tmp_path* with a .git dir."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write(path: Path, content: str = "hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# find_git_root
# ---------------------------------------------------------------------------


def test_find_git_root_with_dir(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert find_git_root(repo) == repo.resolve()
    assert find_git_root(repo / "nested" / "deeper") == repo.resolve()


def test_find_git_root_with_file(tmp_path: Path) -> None:
    # Submodule worktrees have a .git file, not dir.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../.git/modules/foo", encoding="utf-8")
    assert find_git_root(repo) == repo.resolve()
    # Descendants also find it.
    sub = repo / "sub"
    sub.mkdir()
    assert find_git_root(sub) == repo.resolve()


def test_find_git_root_not_in_repo(tmp_path: Path) -> None:
    assert find_git_root(tmp_path) is None


# ---------------------------------------------------------------------------
# discover_project_files — ancestor walk
# ---------------------------------------------------------------------------


def test_nearest_wins_on_ancestor_walk(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "root")
    nested = repo / "pkg" / "sub"
    nested.mkdir(parents=True)
    _write(nested / "AGENTS.md", "nearest")

    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=False)
    files = discover_project_files(nested, config)
    assert len(files) == 1
    path, filename, source = files[0]
    assert path == (nested / "AGENTS.md").resolve()
    assert filename == "AGENTS.md"
    assert source is ConventionSource.PROJECT


def test_walk_stops_at_git_root(tmp_path: Path) -> None:
    outer = tmp_path / "outside"
    outer.mkdir()
    _write(outer / "AGENTS.md", "outside-repo")

    repo = _make_repo(tmp_path / "repo")
    nested = repo / "src"
    nested.mkdir()
    # No AGENTS.md inside the repo — walk must not escape to outer.
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=False)
    files = discover_project_files(nested, config)
    assert files == []


def test_git_file_recognised_as_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../other", encoding="utf-8")
    _write(repo / "AGENTS.md", "ok")

    nested = repo / "deep"
    nested.mkdir()
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=False)
    files = discover_project_files(nested, config)
    assert len(files) == 1


def test_no_git_root_falls_back_to_cwd_only(tmp_path: Path) -> None:
    # tmp_path has no .git. With walk_up_to="git_root" we should
    # effectively look only in cwd.
    _write(tmp_path / "AGENTS.md", "here")
    sub = tmp_path / "sub"
    sub.mkdir()
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=False)
    # cwd is sub, no .git anywhere, no AGENTS.md in sub → no hit.
    assert discover_project_files(sub, config) == []
    # cwd is tmp_path itself → finds AGENTS.md there.
    files = discover_project_files(tmp_path, config)
    assert len(files) == 1


def test_filesystem_root_walks_all_the_way(tmp_path: Path) -> None:
    # We can't reliably test this one by walking to "/", so use a
    # pseudo-root: cwd has no git, no AGENTS.md, parent has one.
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _write(parent / "AGENTS.md", "parent")
    config = ConventionFilesConfig(
        filenames=["AGENTS.md"],
        walk_up_to="filesystem_root",
        search_subdirs=False,
    )
    files = discover_project_files(child, config)
    assert len(files) == 1
    assert files[0][0] == (parent / "AGENTS.md").resolve()


def test_cwd_only_skips_ancestors(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "root")
    sub = repo / "sub"
    sub.mkdir()
    config = ConventionFilesConfig(
        filenames=["AGENTS.md"],
        walk_up_to="cwd_only",
        search_subdirs=False,
    )
    assert discover_project_files(sub, config) == []


# ---------------------------------------------------------------------------
# discover_project_files — nested descent
# ---------------------------------------------------------------------------


def test_search_subdirs_finds_nested_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "pkg" / "a" / "AGENTS.md", "a")
    _write(repo / "pkg" / "b" / "AGENTS.md", "b")

    config = ConventionFilesConfig(
        filenames=["AGENTS.md"],
        search_subdirs=True,
        max_nested_depth=3,
    )
    files = discover_project_files(repo, config)
    paths = {f[0] for f in files}
    assert (repo / "pkg" / "a" / "AGENTS.md").resolve() in paths
    assert (repo / "pkg" / "b" / "AGENTS.md").resolve() in paths


def test_max_nested_depth_caps_descent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    # File at depth 4.
    deep = repo / "a" / "b" / "c" / "d"
    _write(deep / "AGENTS.md", "too deep")
    # File at depth 2 — should be found.
    _write(repo / "a" / "b" / "AGENTS.md", "ok")

    config = ConventionFilesConfig(
        filenames=["AGENTS.md"],
        search_subdirs=True,
        max_nested_depth=2,
    )
    files = discover_project_files(repo, config)
    resolved = {f[0] for f in files}
    assert (repo / "a" / "b" / "AGENTS.md").resolve() in resolved
    assert (deep / "AGENTS.md").resolve() not in resolved


def test_skip_dirs_not_descended(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "node_modules" / "pkg" / "AGENTS.md", "skip me")
    _write(repo / ".venv" / "lib" / "AGENTS.md", "skip me")
    _write(repo / "src" / "AGENTS.md", "keep me")

    config = ConventionFilesConfig(
        filenames=["AGENTS.md"],
        search_subdirs=True,
    )
    files = discover_project_files(repo, config)
    resolved = {f[0] for f in files}
    assert (repo / "src" / "AGENTS.md").resolve() in resolved
    assert (repo / "node_modules" / "pkg" / "AGENTS.md").resolve() not in resolved
    assert (repo / ".venv" / "lib" / "AGENTS.md").resolve() not in resolved


def test_hidden_dirs_not_descended(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / ".github" / "AGENTS.md", "skip")
    _write(repo / "src" / "AGENTS.md", "keep")
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=True)
    files = discover_project_files(repo, config)
    resolved = {f[0] for f in files}
    assert (repo / "src" / "AGENTS.md").resolve() in resolved
    assert (repo / ".github" / "AGENTS.md").resolve() not in resolved


def test_symlinks_not_followed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    _write(outside / "AGENTS.md", "escape")
    (repo / "link").symlink_to(outside)
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=True)
    files = discover_project_files(repo, config)
    assert files == []


# ---------------------------------------------------------------------------
# dedup + ordering
# ---------------------------------------------------------------------------


def test_ancestor_and_nested_dedup_on_resolved_path(tmp_path: Path) -> None:
    # cwd == git root, and the file is in cwd. The ancestor walk finds
    # it; the nested descent must not re-emit it.
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "root")
    # Give the nested walk something else to emit too.
    _write(repo / "pkg" / "AGENTS.md", "nested")
    config = ConventionFilesConfig(filenames=["AGENTS.md"], search_subdirs=True)
    files = discover_project_files(repo, config)
    paths = [f[0] for f in files]
    assert len(paths) == len(set(paths))  # no duplicates
    assert (repo / "AGENTS.md").resolve() in paths
    assert (repo / "pkg" / "AGENTS.md").resolve() in paths


def test_multiple_filenames_each_walked_independently(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "CAIRN.md", "c")
    _write(repo / "AGENTS.md", "a")
    config = ConventionFilesConfig(
        filenames=["CAIRN.md", "AGENTS.md"],
        search_subdirs=False,
    )
    files = discover_project_files(repo, config)
    assert [f[1] for f in files] == ["CAIRN.md", "AGENTS.md"]


def test_empty_filenames_yields_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "x")
    config = ConventionFilesConfig(filenames=[])
    assert discover_project_files(repo, config) == []


def test_disabled_yields_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "x")
    config = ConventionFilesConfig(enabled=False)
    assert discover_project_files(repo, config) == []


# ---------------------------------------------------------------------------
# discover_user_files
# ---------------------------------------------------------------------------


def test_user_level_paths_read_independently(tmp_path: Path) -> None:
    user_a = tmp_path / "home" / "CAIRN.md"
    user_b = tmp_path / "home" / "AGENTS.md"
    _write(user_a, "a")
    _write(user_b, "b")
    files = discover_user_files([str(user_a), str(user_b)])
    assert [f[1] for f in files] == ["CAIRN.md", "AGENTS.md"]
    assert all(f[2] is ConventionSource.USER for f in files)


def test_user_level_missing_skipped_silently(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "CAIRN.md"
    assert discover_user_files([str(missing)]) == []


def test_user_level_non_file_logs_and_skips(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    (tmp_path / "dirname").mkdir()
    with caplog.at_level(logging.WARNING, logger="cairn.conventions._discovery"):
        files = discover_user_files([str(tmp_path / "dirname")])
    assert files == []
    assert any("not a regular file" in rec.message for rec in caplog.records)


def test_user_level_path_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAIRN_TEST_HOME", str(tmp_path))
    _write(tmp_path / "CAIRN.md", "x")
    files = discover_user_files(["$CAIRN_TEST_HOME/CAIRN.md"])
    assert len(files) == 1
    assert files[0][0] == (tmp_path / "CAIRN.md").resolve()
