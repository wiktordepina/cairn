"""Shared fixtures for CLI tests.

Two big concerns:

- The dispatch tests call `main([])`, which triggers
  `cairn.logging.setup_logging()` (it sets `propagate=False` on the
  `cairn` namespace logger so the UI can own the terminal). That
  state would otherwise carry forward to every later test in the
  process and block `caplog` from seeing `cairn.*` log records.
- Subcommand tests need an isolated user-config directory + an
  isolated project tree so they exercise discovery without touching
  the real home / cwd.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _restore_cairn_log_propagation() -> Iterator[None]:
    logger = logging.getLogger("cairn")
    logger.propagate = True
    yield


@pytest.fixture
def isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect `platformdirs` + cwd into *tmp_path* for the test.

    Layout produced:
        <tmp_path>/home/.config/cairn/        (XDG_CONFIG_HOME)
        <tmp_path>/home/.local/share/cairn/   (XDG_DATA_HOME)
        <tmp_path>/project/                   (cwd; not a git repo)

    The user-config file isn't pre-created — tests that need one
    write it themselves so they can vary the contents.
    """
    home = tmp_path / "home"
    xdg_config = home / ".config"
    xdg_data = home / ".local" / "share"
    xdg_config.mkdir(parents=True)
    xdg_data.mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.chdir(project)
    return tmp_path
