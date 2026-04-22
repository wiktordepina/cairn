"""Path derivation for the per-profile data directory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

if TYPE_CHECKING:
    from cairn.config._models import CairnConfig

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _sanitise(profile_name: str) -> str:
    """Validate a profile name for use as a path component."""
    if not profile_name:
        raise ValueError("profile name must not be empty")
    if not _VALID_NAME.match(profile_name):
        raise ValueError(
            f"profile name {profile_name!r} contains disallowed characters; "
            "only alphanumeric, dash, and underscore are permitted"
        )
    return profile_name


def data_dir_for_profile(profile_name: str) -> Path:
    """Return the per-profile data directory `$XDG_DATA_HOME/cairn/<profile>/`."""
    return Path(platformdirs.user_data_dir("cairn")) / _sanitise(profile_name)


def db_path_for_profile(profile_name: str) -> Path:
    """Return the SQLite database path for a profile."""
    return data_dir_for_profile(profile_name) / "cairn.db"


def memory_dir_for_profile(profile_name: str) -> Path:
    """Return the memory root `$XDG_DATA_HOME/cairn/<profile>/memory/`.

    Houses the JSONL observation log (`observations/YYYY-MM-DD.jsonl`)
    and any future on-disk artefacts the memory brick owns.
    """
    return data_dir_for_profile(profile_name) / "memory"


def db_path_for_config(config: CairnConfig) -> Path:
    """Resolve the DB path from a loaded config's active profile.

    Uses `ProfileConfig.name` if set, else falls back to the
    `active_profile` dict key.
    """
    profile = config.active
    name = profile.name or config.active_profile
    return db_path_for_profile(name)
