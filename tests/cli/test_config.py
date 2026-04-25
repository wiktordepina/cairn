"""Tests for `cairn config show` and `cairn config paths`."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from cairn.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_USER_CONFIG_TOML = textwrap.dedent(
    """\
    schema_version = 1
    active_profile = "personal"

    [providers.anthropic]
    api_key = "keyring:cairn:anthropic-api-key"

    [providers.openai]
    api_key = "env:OPENAI_API_KEY"

    [[models]]
    id = "claude-opus-4-7"
    provider = "anthropic"
    display_name = "Claude Opus 4.7"
    context_window = 200000
    max_output_tokens = 32000
    supports_tools = true
    input_cost_per_1m = 15.0
    output_cost_per_1m = 75.0
    roles = ["primary"]

    [[models]]
    id = "claude-haiku-4-5"
    provider = "anthropic"
    display_name = "Claude Haiku 4.5"
    context_window = 200000
    max_output_tokens = 8000
    supports_tools = true
    input_cost_per_1m = 1.0
    output_cost_per_1m = 5.0
    roles = ["utility"]

    [profiles.personal]
    name = "Cairn"
    soul_document_path = "/tmp/soul.md"
    user_context_path = "/tmp/user.md"
    memory_md_path = "/tmp/MEMORY.md"
    primary_model = "role:primary"
    utility_model = "role:utility"
    """
)


def _write_user_config(home: Path, body: str = _USER_CONFIG_TOML) -> Path:
    user_dir = home / "home" / ".config" / "cairn"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / "config.toml"
    path.write_text(body)
    return path


def _write_project_config(home: Path, body: str) -> Path:
    project = home / "project"
    (project / ".git").mkdir(exist_ok=True)
    cairn_dir = project / ".cairn"
    cairn_dir.mkdir(exist_ok=True)
    path = cairn_dir / "config.toml"
    path.write_text(body)
    return path


def _write_local_config(home: Path, body: str) -> Path:
    project = home / "project"
    (project / ".git").mkdir(exist_ok=True)
    cairn_dir = project / ".cairn"
    cairn_dir.mkdir(exist_ok=True)
    path = cairn_dir / "config.local.toml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# `cairn config show`
# ---------------------------------------------------------------------------


class TestConfigShow:
    def test_renders_merged_config_with_layer_header(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        # No keyring backend should be touched by `show`; force the
        # `keyring` import to surface a deterministic backend so probes
        # are reproducible.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output

        out = result.output
        assert "# merged from:" in out
        assert "user " in out
        # Body keeps reference syntax; never the resolved secret.
        assert 'api_key = "keyring:cairn:anthropic-api-key"' in out
        assert 'api_key = "env:OPENAI_API_KEY"' in out

    def test_keyring_present_annotation(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        import keyring

        def _fake_get_password(service: str, key: str) -> str | None:
            return "sk-ant-XXXX" if (service, key) == ("cairn", "anthropic-api-key") else None

        monkeypatch.setattr(keyring, "get_password", _fake_get_password)

        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "→ present in keyring" in result.output
        # Must never resolve the value.
        assert "sk-ant-XXXX" not in result.output

    def test_keyring_missing_annotation(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        import keyring

        monkeypatch.setattr(keyring, "get_password", lambda service, key: None)

        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "→ MISSING" in result.output

    def test_keyring_backend_unavailable_annotation(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        import keyring
        from keyring.errors import KeyringError

        def _raise(service: str, key: str) -> str | None:
            raise KeyringError("backend down")

        monkeypatch.setattr(keyring, "get_password", _raise)

        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "→ keyring backend unavailable" in result.output

    def test_env_set_annotation(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-XXXXX")
        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "→ set" in result.output
        assert "sk-XXXXX" not in result.output  # never resolved

    def test_env_unset_annotation(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "→ unset" in result.output

    def test_profile_override_passes_through(self, isolated_home: Path) -> None:
        body = _USER_CONFIG_TOML + textwrap.dedent(
            """
            [profiles.work]
            name = "Cairn (work)"
            soul_document_path = "/tmp/soul.md"
            user_context_path = "/tmp/user.md"
            memory_md_path = "/tmp/MEMORY.md"
            primary_model = "role:primary"
            utility_model = "role:utility"
            """
        )
        _write_user_config(isolated_home, body)

        result = CliRunner().invoke(app, ["--profile", "work", "config", "show"])
        assert result.exit_code == 0, result.output
        assert 'active_profile = "work"' in result.output

    def test_layer_header_lists_only_loaded_files(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        _write_project_config(
            isolated_home,
            textwrap.dedent(
                """\
                schema_version = 1

                [profiles.personal]
                primary_model = "claude-haiku-4-5"
                """
            ),
        )
        # Note: no local override.
        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 0, result.output
        assert "user" in result.output
        assert "project" in result.output
        assert "local" not in result.output

    def test_validation_failure_exits_one_with_hint(self, isolated_home: Path) -> None:
        _write_user_config(
            isolated_home,
            textwrap.dedent(
                """\
                schema_version = 1
                active_profile = "missing-profile"

                [profiles.personal]
                soul_document_path = "/tmp/soul.md"
                user_context_path = "/tmp/user.md"
                memory_md_path = "/tmp/MEMORY.md"
                primary_model = "role:primary"
                utility_model = "role:utility"
                """
            ),
        )
        result = CliRunner().invoke(app, ["config", "show"])
        assert result.exit_code == 1
        assert "cairn:" in result.stderr
        assert "config validate" in result.stderr


# ---------------------------------------------------------------------------
# `cairn config paths`
# ---------------------------------------------------------------------------


class TestConfigPaths:
    def test_user_only(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        result = CliRunner().invoke(app, ["config", "paths"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "user" in out
        assert "(loaded)" in out
        # No project root → no project / local rows.
        assert "project" not in out
        assert "local" not in out

    def test_all_three_layers_when_in_project(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        _write_project_config(
            isolated_home,
            'schema_version = 1\n[profiles.personal]\nprimary_model = "claude-haiku-4-5"\n',
        )
        # No local file written.
        result = CliRunner().invoke(app, ["config", "paths"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "user" in out and "project" in out and "local" in out
        # Local row should be flagged as missing.
        local_line = next(line for line in out.splitlines() if line.startswith("local"))
        assert "(not present)" in local_line

    def test_user_missing_is_reported_not_present(self, isolated_home: Path) -> None:
        # No user config written, no project tree.
        result = CliRunner().invoke(app, ["config", "paths"])
        assert result.exit_code == 0, result.output
        assert "user" in result.output
        assert "(not present)" in result.output

    def test_local_loaded_status_reported(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        _write_project_config(
            isolated_home,
            'schema_version = 1\n[profiles.personal]\nprimary_model = "claude-haiku-4-5"\n',
        )
        _write_local_config(
            isolated_home,
            "schema_version = 1\n[profiles.personal.budgets]\nper_turn_usd = 1.0\n",
        )
        result = CliRunner().invoke(app, ["config", "paths"])
        assert result.exit_code == 0, result.output
        local_line = next(line for line in result.output.splitlines() if line.startswith("local"))
        assert "(loaded)" in local_line
