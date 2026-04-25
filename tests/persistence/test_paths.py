"""Tests for path derivation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.persistence._paths import (
    _sanitise,
    data_dir_for_profile,
    db_path_for_profile,
    prompt_history_path,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestSanitise:
    def test_valid_alphanumeric(self) -> None:
        assert _sanitise("companion") == "companion"

    def test_valid_with_dash_underscore(self) -> None:
        assert _sanitise("my_profile-2") == "my_profile-2"

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _sanitise("")

    def test_path_separator_rejected(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            _sanitise("foo/bar")

    def test_dotdot_rejected(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            _sanitise("..")

    def test_space_rejected(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            _sanitise("my profile")


class TestPathDerivation:
    def test_data_dir_uses_xdg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = data_dir_for_profile("companion")
        assert path == tmp_path / "cairn" / "companion"

    def test_db_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = db_path_for_profile("work")
        assert path == tmp_path / "cairn" / "work" / "cairn.db"

    def test_invalid_profile_rejected(self) -> None:
        with pytest.raises(ValueError):
            data_dir_for_profile("../escape")

    def test_prompt_history_path_is_per_profile_per_project(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        proj_a = tmp_path / "project_a"
        proj_b = tmp_path / "project_b"
        proj_a.mkdir()
        proj_b.mkdir()
        path_a = prompt_history_path("companion", proj_a)
        path_b = prompt_history_path("companion", proj_b)
        assert path_a.parent == tmp_path / "cairn" / "companion" / "history"
        assert path_a.name.endswith(".jsonl")
        assert path_a != path_b

    def test_prompt_history_path_is_stable_across_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        proj = tmp_path / "stable"
        proj.mkdir()
        assert prompt_history_path("p", proj) == prompt_history_path("p", proj)
