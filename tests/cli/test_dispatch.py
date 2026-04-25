"""Dispatch tests for the `cairn` console-script entry point.

These cover the Typer wiring: --help, --version, the default
no-subcommand UI launch, --profile pass-through, and the entry-point
declaration in `pyproject.toml`. Subcommand-specific behaviour lives
in the per-cluster modules (`test_config.py`, etc.).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from cairn.cli import app, main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _plain(text: str) -> str:
    """Strip ANSI escape sequences so substring assertions survive
    Click 8.2's rich-help rendering on terminals that advertise
    capability (some CI runners do; pytest's local capture doesn't).
    Box-drawing characters stay — they don't fragment substrings."""
    return _ANSI_RE.sub("", text)


class TestCLISmoke:
    def test_help_prints_and_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--help"])
        assert rc == 0
        out = _plain(capsys.readouterr().out)
        assert "Cairn companion CLI." in out
        assert "--profile" in out

    def test_version_prints_and_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--version"])
        assert rc == 0
        out = _plain(capsys.readouterr().out)
        assert out.startswith("cairn ")

    def test_invalid_flag_exits_with_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--nonsense"])
        assert rc == 2
        err = _plain(capsys.readouterr().err)
        assert "--nonsense" in err or "No such option" in err

    def test_launch_is_invoked(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`main([])` hands off to `_bootstrap.launch` and returns its
        exit code without spinning up the Textual app."""
        from typer.testing import CliRunner

        # Seed a valid config so the no-config short-circuit doesn't fire.
        seed = CliRunner().invoke(app, ["config", "init", "--no-prompt"])
        assert seed.exit_code == 0, seed.output

        import cairn.ui._bootstrap as bootstrap

        captured: dict[str, str | None] = {}

        def _fake_launch(*, profile_name: str | None) -> int:
            captured["profile_name"] = profile_name
            return 42

        monkeypatch.setattr(bootstrap, "launch", _fake_launch)
        assert main([]) == 42
        assert captured == {"profile_name": None}

    def test_profile_flag_passes_through_to_launch(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from typer.testing import CliRunner

        seed = CliRunner().invoke(app, ["--profile", "work", "config", "init", "--no-prompt"])
        assert seed.exit_code == 0, seed.output

        import cairn.ui._bootstrap as bootstrap

        captured: dict[str, str | None] = {}

        def _fake_launch(*, profile_name: str | None) -> int:
            captured["profile_name"] = profile_name
            return 0

        monkeypatch.setattr(bootstrap, "launch", _fake_launch)
        assert main(["--profile", "work"]) == 0
        assert captured == {"profile_name": "work"}

    def test_entry_point_is_wired_in_pyproject(self) -> None:
        """`cairn` entry point resolves to `cairn.cli:main`."""
        from importlib.metadata import entry_points

        console = entry_points(group="console_scripts")
        match = next((ep for ep in console if ep.name == "cairn"), None)
        assert match is not None, f"Expected `cairn` console script; got {list(console.names)}"
        assert match.value == "cairn.cli:main"


class TestCLIIsImportable:
    def test_main_module_run_is_wired(self) -> None:
        """`python -m cairn.cli` path works (module imports cleanly)."""
        import cairn.cli

        assert callable(cairn.cli.main)
        # `app` is the Typer entry the rest of the package mounts onto.
        assert hasattr(cairn.cli, "app")
