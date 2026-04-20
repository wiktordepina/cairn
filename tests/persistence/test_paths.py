"""Tests for path derivation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cairn.persistence._paths import (
    _sanitise,
    data_dir_for_profile,
    db_path_for_profile,
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
