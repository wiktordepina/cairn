"""Tests for `cairn.watcher.discover_watch_paths` — runtime path glue."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.config import ConventionFilesConfig
from cairn.conventions import AlwaysTrustGate, ConventionLoader
from cairn.memory import ProfileDocLoader
from cairn.watcher import discover_watch_paths

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write_config(repo: Path) -> Path:
    cairn_dir = repo / ".cairn"
    cairn_dir.mkdir()
    project = cairn_dir / "config.toml"
    project.write_text("schema_version = 1\n", encoding="utf-8")
    return project


def _write_local_config(repo: Path) -> Path:
    local = repo / ".cairn" / "config.local.toml"
    local.write_text("schema_version = 1\n", encoding="utf-8")
    return local


def _convention_loader(repo: Path, config: ConventionFilesConfig) -> ConventionLoader:
    return ConventionLoader(
        config=config,
        trust_gate=AlwaysTrustGate(),
        cwd=repo,
    )


def _profile_doc_loader(tmp_path: Path) -> ProfileDocLoader:
    return ProfileDocLoader(
        soul_document_path=tmp_path / "soul_document.md",
        user_context_path=tmp_path / "user_context.md",
        memory_md_path=tmp_path / "MEMORY.md",
    )


@pytest.fixture
def convention_config() -> ConventionFilesConfig:
    return ConventionFilesConfig(
        enabled=True,
        filenames=["AGENTS.md"],
        user_level_paths=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiscoverWatchPaths:
    def test_picks_up_existing_project_config(
        self, tmp_path: Path, convention_config: ConventionFilesConfig
    ) -> None:
        repo = _make_repo(tmp_path)
        project = _write_config(repo)

        rows = discover_watch_paths(
            convention_files_config=convention_config,
            convention_loader=_convention_loader(repo, convention_config),
            profile_doc_loader=_profile_doc_loader(repo),
            project_dir=repo,
        )

        config_rows = [(c, p) for c, p in rows if c == "config"]
        assert (("config", project)) in [(c, p) for c, p in config_rows]

    def test_includes_local_config_when_present(
        self, tmp_path: Path, convention_config: ConventionFilesConfig
    ) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        local = _write_local_config(repo)

        rows = discover_watch_paths(
            convention_files_config=convention_config,
            convention_loader=_convention_loader(repo, convention_config),
            profile_doc_loader=_profile_doc_loader(repo),
            project_dir=repo,
        )

        config_paths = [p for c, p in rows if c == "config"]
        assert local in config_paths

    def test_picks_up_project_convention_file(
        self, tmp_path: Path, convention_config: ConventionFilesConfig
    ) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        agents = repo / "AGENTS.md"
        agents.write_text("project conventions", encoding="utf-8")

        rows = discover_watch_paths(
            convention_files_config=convention_config,
            convention_loader=_convention_loader(repo, convention_config),
            profile_doc_loader=_profile_doc_loader(repo),
            project_dir=repo,
        )

        convention_paths = [p for c, p in rows if c == "convention"]
        assert agents.resolve() in convention_paths

    def test_picks_up_user_level_convention_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        user_doc = tmp_path / "user-CAIRN.md"
        user_doc.write_text("user-level conventions", encoding="utf-8")

        cfg = ConventionFilesConfig(
            enabled=True,
            filenames=["AGENTS.md"],
            user_level_paths=[str(user_doc)],
        )

        rows = discover_watch_paths(
            convention_files_config=cfg,
            convention_loader=_convention_loader(repo, cfg),
            profile_doc_loader=_profile_doc_loader(repo),
            project_dir=repo,
        )

        convention_paths = [p for c, p in rows if c == "convention"]
        assert user_doc.resolve() in convention_paths

    def test_skips_conventions_when_disabled(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        agents = repo / "AGENTS.md"
        agents.write_text("ignored", encoding="utf-8")
        cfg = ConventionFilesConfig(enabled=False)

        rows = discover_watch_paths(
            convention_files_config=cfg,
            convention_loader=_convention_loader(repo, cfg),
            profile_doc_loader=_profile_doc_loader(repo),
            project_dir=repo,
        )

        assert all(c != "convention" for c, _ in rows)

    def test_picks_up_existing_profile_docs_only(
        self, tmp_path: Path, convention_config: ConventionFilesConfig
    ) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        soul = repo / "soul_document.md"
        soul.write_text("soul", encoding="utf-8")
        # user_context + MEMORY are missing — only soul should appear.
        loader = ProfileDocLoader(
            soul_document_path=soul,
            user_context_path=repo / "user_context.md",
            memory_md_path=repo / "MEMORY.md",
        )

        rows = discover_watch_paths(
            convention_files_config=convention_config,
            convention_loader=_convention_loader(repo, convention_config),
            profile_doc_loader=loader,
            project_dir=repo,
        )

        doc_paths = [p for c, p in rows if c == "profile_doc"]
        assert doc_paths == [soul]

    def test_stable_ordering(
        self, tmp_path: Path, convention_config: ConventionFilesConfig
    ) -> None:
        repo = _make_repo(tmp_path)
        _write_config(repo)
        (repo / "AGENTS.md").write_text("conv", encoding="utf-8")
        (repo / "soul_document.md").write_text("soul", encoding="utf-8")

        rows = discover_watch_paths(
            convention_files_config=convention_config,
            convention_loader=_convention_loader(repo, convention_config),
            profile_doc_loader=ProfileDocLoader(
                soul_document_path=repo / "soul_document.md",
                user_context_path=repo / "user_context.md",
                memory_md_path=repo / "MEMORY.md",
            ),
            project_dir=repo,
        )

        categories = [c for c, _ in rows]
        # Ordering invariant: every "config" appears before every
        # "convention" before every "profile_doc".
        first_convention = (
            categories.index("convention") if "convention" in categories else len(categories)
        )
        first_profile = (
            categories.index("profile_doc") if "profile_doc" in categories else len(categories)
        )
        last_config = (
            len(categories) - 1 - categories[::-1].index("config")
            if "config" in categories
            else -1
        )
        assert last_config < first_convention
        assert first_convention < first_profile or "profile_doc" not in categories
