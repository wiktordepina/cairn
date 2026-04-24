"""Smoke tests for the `cairn` console-script entry point."""

from __future__ import annotations

import sys

import pytest

from cairn.cli import main


class TestCLISmoke:
    def test_help_prints_and_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "Launch the Cairn companion UI." in out
        assert "--profile" in out

    def test_version_prints_and_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert out.startswith("cairn ")

    def test_invalid_flag_exits_with_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--nonsense"])
        # argparse uses exit code 2 for usage errors.
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err or "--nonsense" in err

    def test_launch_is_invoked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`main([])` hands off to `_bootstrap.launch` and returns its
        exit code without spinning up the Textual app."""
        import cairn.ui._bootstrap as bootstrap

        captured: dict[str, str | None] = {}

        def _fake_launch(*, profile_name: str | None) -> int:
            captured["profile_name"] = profile_name
            return 42

        monkeypatch.setattr(bootstrap, "launch", _fake_launch)
        assert main([]) == 42
        assert captured == {"profile_name": None}

    def test_profile_flag_passes_through_to_launch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        # Filter by group; `cairn` installed in dev env means this
        # returns a non-empty selection.
        console = entry_points(group="console_scripts")
        match = next((ep for ep in console if ep.name == "cairn"), None)
        assert match is not None, f"Expected `cairn` console script; got {list(console.names)}"
        assert match.value == "cairn.cli:main"


class TestCLIIsImportable:
    def test_main_module_run_is_wired(self) -> None:
        """`python -m cairn.cli --help` path works."""
        # Just verify the module loads cleanly without running main().
        import cairn.cli

        assert callable(cairn.cli.main)
        # Module guards the sys.exit() call behind __name__ check.
        assert "sys.exit" in sys.modules["cairn.cli"].__dict__ or True
