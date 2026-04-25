"""Tests for `cairn config init` and the no-config short-circuit."""

from __future__ import annotations

import getpass
from typing import TYPE_CHECKING

import keyring
from typer.testing import CliRunner

from cairn.cli import app, main
from cairn.config._loader import load_config, user_config_path

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ---------------------------------------------------------------------------
# `cairn config init` — happy paths
# ---------------------------------------------------------------------------


class TestConfigInitNoPrompt:
    def test_writes_loadable_config(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert result.exit_code == 0, result.output
        assert user_config_path().is_file()
        # And the resulting config validates.
        cfg = load_config()
        assert cfg.active.name == "Cairn"

    def test_writes_three_stub_files(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert result.exit_code == 0, result.output
        config_dir = user_config_path().parent
        for name in ("soul_document.md", "user_context.md", "MEMORY.md"):
            path = config_dir / name
            assert path.is_file(), f"expected stub at {path}"
            assert path.read_text().strip() != ""

    def test_stub_uses_companion_name_in_body(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert result.exit_code == 0
        soul = (user_config_path().parent / "soul_document.md").read_text()
        assert "Cairn" in soul

    def test_refuses_to_clobber_existing_config(self, isolated_home: Path) -> None:
        first = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert first.exit_code == 0

        second = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert second.exit_code == 1
        assert "already exists" in second.stderr

    def test_preserves_handedited_stubs_on_re_init(self, isolated_home: Path) -> None:
        # First init.
        first = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert first.exit_code == 0

        # Hand-edit the soul document, then nuke just the config.toml.
        config_dir = user_config_path().parent
        soul = config_dir / "soul_document.md"
        soul.write_text("# my hand-edited identity\n")
        user_config_path().unlink()

        # Re-run init — config gets recreated, stubs are kept.
        second = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert second.exit_code == 0
        assert "kept existing" in second.output
        assert soul.read_text() == "# my hand-edited identity\n"

    def test_profile_flag_renames_default_profile(self, isolated_home: Path) -> None:
        result = CliRunner().invoke(app, ["--profile", "work", "config", "init", "--no-prompt"])
        assert result.exit_code == 0, result.output
        cfg = load_config()
        assert cfg.active_profile == "work"
        assert "work" in cfg.profiles


# ---------------------------------------------------------------------------
# `cairn config init` — interactive
# ---------------------------------------------------------------------------


class TestConfigInitPrompt:
    def test_elicits_companion_name_and_provider(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Prompt sequence: companion name → "Atlas", provider → "2" (openai),
        # store key → "n".
        result = CliRunner().invoke(app, ["config", "init"], input="Atlas\n2\nn\n")
        assert result.exit_code == 0, result.output

        cfg = load_config()
        assert cfg.active.name == "Atlas"
        assert "openai" in cfg.providers

    def test_invalid_provider_choice_reprompts(
        self,
        isolated_home: Path,
    ) -> None:
        # Reject "9" then accept "1" (anthropic). Companion name = bare Enter (default).
        result = CliRunner().invoke(app, ["config", "init"], input="\n9\n1\nn\n")
        assert result.exit_code == 0, result.output
        assert "(choose 1.." in result.output

    def test_non_numeric_provider_reprompts(
        self,
        isolated_home: Path,
    ) -> None:
        result = CliRunner().invoke(app, ["config", "init"], input="\nasdf\n1\nn\n")
        assert result.exit_code == 0, result.output
        assert "(enter a number)" in result.output

    def test_stores_key_in_keyring_when_confirmed(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            keyring,
            "set_password",
            lambda s, k, v: store.update({(s, k): v}),
        )
        monkeypatch.setattr(getpass, "getpass", lambda prompt: "sk-stored")

        result = CliRunner().invoke(app, ["config", "init"], input="\n1\ny\n")
        assert result.exit_code == 0, result.output
        assert store == {("cairn", "anthropic-api-key"): "sk-stored"}


# ---------------------------------------------------------------------------
# No-config short-circuit
# ---------------------------------------------------------------------------


class TestNoConfigShortCircuit:
    def test_default_launch_with_no_config_redirects(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Prove we never reach the bootstrap.
        import cairn.ui._bootstrap as bootstrap

        called = False

        def _should_not_be_called(**_: object) -> int:
            nonlocal called
            called = True
            return 99

        monkeypatch.setattr(bootstrap, "launch", _should_not_be_called)

        rc = main([])
        assert rc == 0
        assert called is False

    def test_default_launch_with_config_proceeds_to_bootstrap(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed a valid config first.
        first = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert first.exit_code == 0

        import cairn.ui._bootstrap as bootstrap

        captured: dict[str, str | None] = {}

        def _fake_launch(*, profile_name: str | None) -> int:
            captured["profile_name"] = profile_name
            return 0

        monkeypatch.setattr(bootstrap, "launch", _fake_launch)
        rc = main([])
        assert rc == 0
        assert captured == {"profile_name": None}
