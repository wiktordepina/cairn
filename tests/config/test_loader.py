"""Tests for config loading — TOML parsing, merge, discovery, profile resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cairn.config._loader import (
    ConfigError,
    config_paths,
    discover_project_root,
    load_config,
    load_raw,
)

from .conftest import SAMPLE_TOML

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadRaw:
    def test_valid_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('schema_version = 1\nactive_profile = "test"\n')
        data = load_raw(p)
        assert data["schema_version"] == 1

    def test_missing_schema_version(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('active_profile = "test"\n')
        with pytest.raises(ConfigError, match="schema_version"):
            load_raw(p)

    def test_future_version_warns(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        p = tmp_path / "config.toml"
        p.write_text('schema_version = 999\nactive_profile = "test"\n')
        import logging

        with caplog.at_level(logging.WARNING):
            data = load_raw(p)
        assert data["schema_version"] == 999
        assert "999" in caplog.text


class TestDiscoverProjectRoot:
    def test_finds_cairn_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        (project / ".git").mkdir()
        cairn_dir = project / ".cairn"
        cairn_dir.mkdir()
        (cairn_dir / "config.toml").write_text('schema_version = 1\nactive_profile = "x"\n')

        result = discover_project_root(project)
        assert result == project

    def test_walks_up(self, tmp_path: Path) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        (project / ".git").mkdir()
        cairn_dir = project / ".cairn"
        cairn_dir.mkdir()
        (cairn_dir / "config.toml").write_text('schema_version = 1\nactive_profile = "x"\n')

        subdir = project / "src" / "deep"
        subdir.mkdir(parents=True)

        result = discover_project_root(subdir)
        assert result == project

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        project = tmp_path / "repo"
        project.mkdir()
        (project / ".git").mkdir()
        # No .cairn dir

        result = discover_project_root(project)
        assert result is None


class TestConfigPaths:
    def test_user_always_present(self) -> None:
        paths = config_paths(project_dir=None)
        assert any(layer == "user" for layer, _ in paths)

    def test_project_and_local_found(self, config_dirs: dict[str, Path]) -> None:
        paths = config_paths(project_dir=config_dirs["project"])
        layers = [layer for layer, _ in paths]
        assert "project" in layers
        assert "local" in layers


class TestLoadConfig:
    def test_full_load(self, config_dirs: dict[str, Path]) -> None:
        with patch(
            "cairn.config._loader.user_config_path",
            return_value=config_dirs["user_config"],
        ):
            config = load_config(project_dir=config_dirs["project"])

        assert config.schema_version == 1
        assert config.active_profile == "personal"
        # Project override: primary_model changed
        assert config.active.primary_model == "claude-haiku-4-5"
        # Local override: budget changed
        assert config.active.budgets.per_turn_usd == 1.00
        # User default preserved
        assert config.active.name == "pendragon"

    def test_profile_cli_override(self, tmp_path: Path) -> None:
        """CLI --profile should override the config's active_profile."""
        user_config = tmp_path / "config.toml"
        toml = SAMPLE_TOML.replace(
            "[profiles.personal]",
            "[profiles.work]\n"
            'soul_document_path = "/tmp/soul.md"\n'
            'user_context_path = "/tmp/user.md"\n'
            'memory_md_path = "/tmp/MEMORY.md"\n'
            'primary_model = "role:primary"\n'
            'utility_model = "role:utility"\n'
            'memory_space = "work"\n\n'
            "[profiles.personal]",
        )
        user_config.write_text(toml)

        with patch(
            "cairn.config._loader.user_config_path",
            return_value=user_config,
        ):
            config = load_config(profile="work")

        assert config.active_profile == "work"
        assert config.active.memory_space == "work"

    def test_provider_name_injected(self, config_dirs: dict[str, Path]) -> None:
        with patch(
            "cairn.config._loader.user_config_path",
            return_value=config_dirs["user_config"],
        ):
            config = load_config(project_dir=config_dirs["project"])

        assert config.providers["anthropic"].name == "anthropic"

    def test_no_config_raises(self, tmp_path: Path) -> None:
        with (
            patch(
                "cairn.config._loader.user_config_path",
                return_value=tmp_path / "nonexistent" / "config.toml",
            ),
            pytest.raises(ConfigError, match="No config files found"),
        ):
            load_config(project_dir=tmp_path / "also-nonexistent")
