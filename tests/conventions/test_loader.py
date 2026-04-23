"""Tests for `ConventionLoader`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.config import ConventionFilesConfig
from cairn.conventions import (
    AllowlistStore,
    AllowlistTrustGate,
    AlwaysTrustGate,
    ConventionLoader,
    ConventionSource,
    DenyingPromptTrustGate,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write(path: Path, content: str = "hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_disabled_short_circuits(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md")
    loader = ConventionLoader(
        config=ConventionFilesConfig(enabled=False),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    assert await loader.load() == []


@pytest.mark.asyncio
async def test_loads_project_file_when_trusted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo / "AGENTS.md", "# project rules\nBuild with `make`.")
    loader = ConventionLoader(
        config=ConventionFilesConfig(filenames=["AGENTS.md"]),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    files = await loader.load()
    assert len(files) == 1
    assert files[0].filename == "AGENTS.md"
    assert "project rules" in files[0].content
    assert files[0].source is ConventionSource.PROJECT


@pytest.mark.asyncio
async def test_deny_drops_project_files_keeps_user_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _write(repo / "AGENTS.md", "project")
    user_cairn = tmp_path / "home" / "CAIRN.md"
    _write(user_cairn, "user baseline")

    loader = ConventionLoader(
        config=ConventionFilesConfig(
            filenames=["AGENTS.md"],
            user_level_paths=[str(user_cairn)],
        ),
        trust_gate=DenyingPromptTrustGate(),
        cwd=repo,
    )
    files = await loader.load()
    assert len(files) == 1
    assert files[0].filename == "CAIRN.md"
    assert files[0].source is ConventionSource.USER


@pytest.mark.asyncio
async def test_cache_avoids_reread(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    target = repo / "AGENTS.md"
    _write(target, "first")
    loader = ConventionLoader(
        config=ConventionFilesConfig(filenames=["AGENTS.md"]),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    first = await loader.load()
    target.write_text("second", encoding="utf-8")
    second = await loader.load()
    assert first == second
    assert "first" in second[0].content


@pytest.mark.asyncio
async def test_invalidate_forces_reread(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    target = repo / "AGENTS.md"
    _write(target, "first")
    loader = ConventionLoader(
        config=ConventionFilesConfig(filenames=["AGENTS.md"]),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    await loader.load()
    target.write_text("second", encoding="utf-8")
    loader.invalidate()
    files = await loader.load()
    assert "second" in files[0].content


@pytest.mark.asyncio
async def test_size_cap_paragraph_truncation(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    body = "A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500
    _write(repo / "AGENTS.md", body)
    original_bytes = len(body.encode("utf-8"))
    loader = ConventionLoader(
        config=ConventionFilesConfig(
            filenames=["AGENTS.md"],
            max_bytes_per_file=600,  # forces truncation at second paragraph boundary
        ),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    files = await loader.load()
    assert len(files) == 1
    f = files[0]
    assert f.truncated_from == original_bytes
    assert "truncated: original was" in f.content
    # Must have truncated at a paragraph boundary — no 'C' block.
    assert "C" * 500 not in f.content


@pytest.mark.asyncio
async def test_binary_file_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "AGENTS.md").write_bytes(b"\x00\x01\x02garbage\x00")
    loader = ConventionLoader(
        config=ConventionFilesConfig(filenames=["AGENTS.md"]),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    assert await loader.load() == []


@pytest.mark.asyncio
async def test_user_files_ordered_before_project(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _write(repo / "CAIRN.md", "project")
    user_cairn = tmp_path / "home" / "CAIRN.md"
    _write(user_cairn, "user")
    loader = ConventionLoader(
        config=ConventionFilesConfig(
            filenames=["CAIRN.md"],
            user_level_paths=[str(user_cairn)],
        ),
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )
    files = await loader.load()
    assert [f.source for f in files] == [
        ConventionSource.USER,
        ConventionSource.PROJECT,
    ]


@pytest.mark.asyncio
async def test_allowlist_gate_blocks_untrusted_project(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _write(repo / "AGENTS.md", "project")
    store = AllowlistStore(tmp_path / "trusted.toml")
    # store is empty — repo not trusted.
    loader = ConventionLoader(
        config=ConventionFilesConfig(filenames=["AGENTS.md"]),
        trust_gate=AllowlistTrustGate(store),
        cwd=repo,
    )
    assert await loader.load() == []
