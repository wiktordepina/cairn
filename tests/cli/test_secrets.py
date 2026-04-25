"""Tests for `cairn config secret set/list/delete`."""

from __future__ import annotations

import getpass
import textwrap
from typing import TYPE_CHECKING

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError
from typer.testing import CliRunner

from cairn.cli import app

if TYPE_CHECKING:
    from pathlib import Path


_USER_CONFIG_TOML = textwrap.dedent(
    """\
    schema_version = 1
    active_profile = "personal"

    [providers.anthropic]
    api_key = "keyring:cairn:anthropic-api-key"

    [providers.openai]
    api_key = "env:OPENAI_API_KEY"

    [providers.openrouter]
    api_key = "prompt:OpenRouter API key"

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


@pytest.fixture
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """Replace the top-level `keyring` API with an in-memory dict."""
    store: dict[tuple[str, str], str] = {}

    def _get(service: str, key: str) -> str | None:
        return store.get((service, key))

    def _set(service: str, key: str, value: str) -> None:
        store[(service, key)] = value

    def _delete(service: str, key: str) -> None:
        if (service, key) not in store:
            raise PasswordDeleteError(f"no entry for {service}/{key}")
        del store[(service, key)]

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store


# ---------------------------------------------------------------------------
# `cairn config secret set`
# ---------------------------------------------------------------------------


class TestSecretSet:
    def test_writes_keyring_entry(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "sk-test-XXXX")
        result = CliRunner().invoke(
            app, ["config", "secret", "set", "keyring:cairn:anthropic-api-key"]
        )
        assert result.exit_code == 0, result.output
        assert memory_keyring == {("cairn", "anthropic-api-key"): "sk-test-XXXX"}
        assert "✓ Stored in keyring" in result.output

    def test_rejects_env_scheme_with_remediation(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        result = CliRunner().invoke(app, ["config", "secret", "set", "env:OPENAI_API_KEY"])
        assert result.exit_code == 1
        assert "env" in result.stderr
        assert "OPENAI_API_KEY" in result.stderr
        assert memory_keyring == {}

    def test_rejects_prompt_scheme(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        result = CliRunner().invoke(app, ["config", "secret", "set", "prompt:Google key"])
        assert result.exit_code == 1
        assert "prompt" in result.stderr

    def test_rejects_literal_scheme(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        result = CliRunner().invoke(app, ["config", "secret", "set", "literal:abc"])
        assert result.exit_code == 1
        assert "literal" in result.stderr

    def test_invalid_reference_syntax(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        result = CliRunner().invoke(app, ["config", "secret", "set", "garbage"])
        assert result.exit_code == 1
        assert "invalid secret reference" in result.stderr

    def test_overwrite_confirm_default_no(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        memory_keyring[("cairn", "anthropic-api-key")] = "old-value"
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "should-not-be-set")
        # Default `[y/N]` → bare Enter on stdin = no.
        result = CliRunner().invoke(
            app,
            ["config", "secret", "set", "keyring:cairn:anthropic-api-key"],
            input="\n",
        )
        assert result.exit_code == 0, result.output
        assert "left unchanged" in result.output
        assert memory_keyring[("cairn", "anthropic-api-key")] == "old-value"

    def test_overwrite_confirm_yes(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        memory_keyring[("cairn", "anthropic-api-key")] = "old-value"
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "new-value")
        result = CliRunner().invoke(
            app,
            ["config", "secret", "set", "keyring:cairn:anthropic-api-key"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        assert memory_keyring[("cairn", "anthropic-api-key")] == "new-value"

    def test_keyring_backend_unavailable_exits_4(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _raise(service: str, key: str) -> str | None:
            raise KeyringError("backend down")

        monkeypatch.setattr(keyring, "get_password", _raise)
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "should-not-reach")
        result = CliRunner().invoke(
            app, ["config", "secret", "set", "keyring:cairn:anthropic-api-key"]
        )
        assert result.exit_code == 4
        assert "keyring backend unavailable" in result.stderr

    def test_empty_value_is_refused(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "")
        result = CliRunner().invoke(
            app, ["config", "secret", "set", "keyring:cairn:anthropic-api-key"]
        )
        assert result.exit_code == 1
        assert "empty value" in result.stderr
        assert memory_keyring == {}


# ---------------------------------------------------------------------------
# `cairn config secret list`
# ---------------------------------------------------------------------------


class TestSecretList:
    def test_shows_every_scheme_with_status(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(isolated_home)
        memory_keyring[("cairn", "anthropic-api-key")] = "stored"
        monkeypatch.setenv("OPENAI_API_KEY", "set-in-env")

        result = CliRunner().invoke(app, ["config", "secret", "list"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "keyring:cairn:anthropic-api-key" in out
        assert "present in keyring" in out
        assert "env:OPENAI_API_KEY" in out
        assert "set" in out
        assert "prompt:OpenRouter API key" in out
        assert "prompts on first use" in out
        # Path annotation present.
        assert "(providers.anthropic.api_key)" in out
        # No resolved values leak.
        assert "stored" not in out
        assert "set-in-env" not in out

    def test_no_secrets_in_config(self, isolated_home: Path) -> None:
        _write_user_config(
            isolated_home,
            textwrap.dedent(
                """\
                schema_version = 1
                active_profile = "personal"

                [profiles.personal]
                soul_document_path = "/tmp/soul.md"
                user_context_path = "/tmp/user.md"
                memory_md_path = "/tmp/MEMORY.md"
                primary_model = "role:primary"
                utility_model = "role:utility"
                """
            ),
        )
        result = CliRunner().invoke(app, ["config", "secret", "list"])
        assert result.exit_code == 0, result.output
        assert "no secret references" in result.output

    def test_validation_failure_exits_one(self, isolated_home: Path) -> None:
        # No user config — load_config will raise ConfigError.
        result = CliRunner().invoke(app, ["config", "secret", "list"])
        assert result.exit_code == 1
        assert "cairn:" in result.stderr


# ---------------------------------------------------------------------------
# `cairn config secret delete`
# ---------------------------------------------------------------------------


class TestSecretDelete:
    def test_yes_removes_entry(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        memory_keyring[("cairn", "anthropic-api-key")] = "stored"
        result = CliRunner().invoke(
            app,
            ["config", "secret", "delete", "keyring:cairn:anthropic-api-key"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        assert memory_keyring == {}
        assert "✓ Removed." in result.output

    def test_no_aborts(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        memory_keyring[("cairn", "anthropic-api-key")] = "stored"
        result = CliRunner().invoke(
            app,
            ["config", "secret", "delete", "keyring:cairn:anthropic-api-key"],
            input="\n",
        )
        assert result.exit_code == 0, result.output
        assert memory_keyring == {("cairn", "anthropic-api-key"): "stored"}
        assert "Aborted." in result.output

    def test_missing_entry_exits_one(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        # Memory keyring is empty.
        result = CliRunner().invoke(
            app,
            ["config", "secret", "delete", "keyring:cairn:nonexistent"],
            input="y\n",
        )
        assert result.exit_code == 1
        assert "no keyring entry" in result.stderr

    def test_rejects_non_keyring_scheme(
        self,
        isolated_home: Path,
        memory_keyring: dict[tuple[str, str], str],
    ) -> None:
        result = CliRunner().invoke(app, ["config", "secret", "delete", "env:OPENAI_API_KEY"])
        assert result.exit_code == 1
        assert "env" in result.stderr
