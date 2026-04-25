"""Tests for `cairn config validate` and `cairn config migrate`."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.config._migrations import _MIGRATIONS, migration

if TYPE_CHECKING:
    from pathlib import Path


_MINIMAL_CONFIG = textwrap.dedent(
    """\
    schema_version = 1
    active_profile = "personal"

    [providers.anthropic]
    api_key = "keyring:cairn:anthropic-api-key"

    [profiles.personal]
    name = "Cairn"
    soul_document_path = "/tmp/soul.md"
    user_context_path = "/tmp/user.md"
    memory_md_path = "/tmp/MEMORY.md"
    primary_model = "role:primary"
    utility_model = "role:utility"
    """
)


def _write_user_config(home: Path, body: str = _MINIMAL_CONFIG) -> Path:
    user_dir = home / "home" / ".config" / "cairn"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / "config.toml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# `cairn config validate`
# ---------------------------------------------------------------------------


class TestConfigValidate:
    def test_silent_on_success(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        result = CliRunner().invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        assert result.stdout == ""

    def test_exits_one_on_validation_failure(self, isolated_home: Path) -> None:
        _write_user_config(
            isolated_home,
            textwrap.dedent(
                """\
                schema_version = 1
                active_profile = "missing"

                [profiles.personal]
                soul_document_path = "/tmp/soul.md"
                user_context_path = "/tmp/user.md"
                memory_md_path = "/tmp/MEMORY.md"
                primary_model = "role:primary"
                utility_model = "role:utility"
                """
            ),
        )
        result = CliRunner().invoke(app, ["config", "validate"])
        assert result.exit_code == 1
        assert "cairn:" in result.stderr
        # Validation failure message bubbled from `ConfigError`.
        assert "missing" in result.stderr

    def test_exits_one_when_no_config_present(self, isolated_home: Path) -> None:
        # No user config written.
        result = CliRunner().invoke(app, ["config", "validate"])
        assert result.exit_code == 1
        assert "cairn:" in result.stderr

    def test_profile_flag_flows_through(self, isolated_home: Path) -> None:
        _write_user_config(
            isolated_home,
            _MINIMAL_CONFIG
            + textwrap.dedent(
                """
                [profiles.work]
                soul_document_path = "/tmp/soul.md"
                user_context_path = "/tmp/user.md"
                memory_md_path = "/tmp/MEMORY.md"
                primary_model = "role:primary"
                utility_model = "role:utility"
                """
            ),
        )
        result = CliRunner().invoke(app, ["--profile", "work", "config", "validate"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# `cairn config migrate`
# ---------------------------------------------------------------------------


class TestConfigMigrateNoOp:
    def test_reports_current_layer(self, isolated_home: Path) -> None:
        _write_user_config(isolated_home)
        result = CliRunner().invoke(app, ["config", "migrate"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "schema_version=1 (current — no action)" in out
        assert "nothing to migrate." in out

    def test_no_layers_present(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["config", "migrate"])
        assert result.exit_code == 0, result.output
        assert "no config files discovered" in result.output

    def test_write_flag_no_op_leaves_files_untouched(self, isolated_home: Path) -> None:
        path = _write_user_config(isolated_home)
        before = path.read_bytes()
        result = CliRunner().invoke(app, ["config", "migrate", "--write"])
        assert result.exit_code == 0, result.output
        # No migration → no rewrite, no backup.
        assert path.read_bytes() == before
        assert not list(path.parent.glob("*.pre-migrate-*"))


@pytest.fixture
def _temporary_v1_to_v2_migration() -> object:
    """Register a v1 → v2 migration for the test, restoring the registry after."""
    saved = dict(_MIGRATIONS)

    @migration(from_version=1, to_version=2)
    def _bump(raw: dict[str, object]) -> dict[str, object]:
        result = dict(raw)
        result["schema_version"] = 2
        result["new_field"] = "added"
        return result

    yield _bump
    _MIGRATIONS.clear()
    _MIGRATIONS.update(saved)


class TestConfigMigrateWithRegisteredMigration:
    """Drive the dry-run / write paths via a temporary v1 → v2 migration.

    The CURRENT_SCHEMA_VERSION constant stays 1, so we monkey-patch
    it for the duration of the test. This proves the dry-run + write
    + backup mechanics independent of any concrete schema work.
    """

    def test_dry_run_reports_would_migrate(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        _temporary_v1_to_v2_migration: object,
    ) -> None:
        from cairn.cli import _config as cli_config
        from cairn.config import _migrations as mig_module
        from cairn.config import _models as models_module

        monkeypatch.setattr(models_module, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(mig_module, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(cli_config, "CURRENT_SCHEMA_VERSION", 2)

        path = _write_user_config(isolated_home)
        before = path.read_bytes()

        result = CliRunner().invoke(app, ["config", "migrate"])
        assert result.exit_code == 0, result.output
        assert "schema_version=1 → 2 (would migrate)" in result.output
        # Dry-run never touches the file.
        assert path.read_bytes() == before

    def test_write_applies_and_backs_up(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        _temporary_v1_to_v2_migration: object,
    ) -> None:
        from cairn.cli import _config as cli_config
        from cairn.config import _migrations as mig_module
        from cairn.config import _models as models_module

        monkeypatch.setattr(models_module, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(mig_module, "CURRENT_SCHEMA_VERSION", 2)
        monkeypatch.setattr(cli_config, "CURRENT_SCHEMA_VERSION", 2)

        path = _write_user_config(isolated_home)
        result = CliRunner().invoke(app, ["config", "migrate", "--write"])
        assert result.exit_code == 0, result.output
        assert "schema_version=1 → 2 (migrated)" in result.output

        # Body now lives at v2 with the new field.
        contents = path.read_text()
        assert "schema_version = 2" in contents
        assert 'new_field = "added"' in contents

        # Backup file at <name>.pre-migrate-1 exists, holds the v1 body.
        backup = path.with_name(path.name + ".pre-migrate-1")
        assert backup.is_file()
        assert "schema_version = 1" in backup.read_text()
