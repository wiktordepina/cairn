"""Config loading — TOML parsing, schema checking, three-tier merge, profile resolution."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

import platformdirs

from cairn.config._merge import deep_merge
from cairn.config._migrations import migrate
from cairn.config._models import CURRENT_SCHEMA_VERSION, CairnConfig

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def user_config_dir() -> Path:
    """Return the user-level config directory (XDG-aware)."""
    return Path(platformdirs.user_config_dir("cairn"))


def user_config_path() -> Path:
    """Return the path to the user-level config file."""
    return user_config_dir() / "config.toml"


def discover_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* to the git root, looking for ``.cairn/config.toml``.

    Returns the directory containing ``.cairn/`` if found, else ``None``.
    The first match wins; nested ``.cairn/`` configs are not merged.
    """
    current = (start or Path.cwd()).resolve()

    # Find the git root as an upper boundary
    git_root: Path | None = None
    check = current
    while True:
        if (check / ".git").exists():
            git_root = check
            break
        parent = check.parent
        if parent == check:
            break
        check = parent

    # Walk up from start to git_root (or filesystem root)
    boundary = git_root or current.root  # type: ignore[arg-type]
    check = current
    while True:
        if (check / ".cairn" / "config.toml").is_file():
            return check
        if check == boundary:
            break
        parent = check.parent
        if parent == check:
            break
        check = parent

    return None


def config_paths(
    project_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    """Return the ordered list of config file paths to load.

    Each entry is ``(layer_name, path)`` where layer_name is one of
    ``"user"``, ``"project"``, or ``"local"``.
    """
    paths: list[tuple[str, Path]] = [("user", user_config_path())]

    root = project_dir or discover_project_root()
    if root is not None:
        project_config = root / ".cairn" / "config.toml"
        if project_config.is_file():
            paths.append(("project", project_config))

        local_config = root / ".cairn" / "config.local.toml"
        if local_config.is_file():
            paths.append(("local", local_config))

    return paths


# ---------------------------------------------------------------------------
# Raw TOML loading
# ---------------------------------------------------------------------------


def load_raw(path: Path) -> dict[str, Any]:
    """Load a single TOML config file and check its schema version.

    Raises ``ConfigError`` on missing ``schema_version``.
    Logs a warning if the version is newer than we know about.
    """
    with path.open("rb") as f:
        data = tomllib.load(f)

    if "schema_version" not in data:
        raise ConfigError(f"{path}: missing required field 'schema_version'")

    version = data["schema_version"]
    if not isinstance(version, int):
        raise ConfigError(
            f"{path}: schema_version must be an integer, got {type(version).__name__}"
        )

    if version > CURRENT_SCHEMA_VERSION:
        logger.warning(
            "Config %s has schema_version %d, but cairn only knows up to %d. "
            "Some fields may be ignored.",
            path,
            version,
            CURRENT_SCHEMA_VERSION,
        )

    return data


# ---------------------------------------------------------------------------
# Provider name injection
# ---------------------------------------------------------------------------


def _inject_provider_names(raw: dict[str, Any]) -> None:
    """Populate each provider's ``name`` field from its dict key."""
    providers: dict[str, Any] | None = raw.get("providers")  # type: ignore[assignment]
    if isinstance(providers, dict):
        for key, value in providers.items():
            if isinstance(value, dict):
                value.setdefault("name", key)  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Full config loading
# ---------------------------------------------------------------------------


def load_config(
    *,
    profile: str | None = None,
    project_dir: Path | None = None,
) -> CairnConfig:
    """Load, merge, migrate, and validate the full cairn configuration.

    Args:
        profile: CLI ``--profile`` override. Takes precedence over all layers.
        project_dir: Explicit project root. If ``None``, discovered automatically.

    Returns:
        A frozen ``CairnConfig`` instance ready for use.

    Raises:
        ConfigError: If no config files are found or validation fails.
    """
    paths = config_paths(project_dir=project_dir)

    # Load and merge all layers
    merged: dict[str, Any] = {}
    loaded_any = False
    for layer_name, path in paths:
        if not path.is_file():
            logger.debug("Config layer %s not found at %s, skipping.", layer_name, path)
            continue
        logger.debug("Loading config layer %s from %s", layer_name, path)
        raw = load_raw(path)
        merged = deep_merge(merged, raw)
        loaded_any = True

    if not loaded_any:
        raise ConfigError(
            f"No config files found. Expected at least a user config at {user_config_path()}"
        )

    # Apply schema migrations
    merged = migrate(merged)

    # CLI profile override
    if profile is not None:
        merged["active_profile"] = profile

    # Inject provider names from dict keys
    _inject_provider_names(merged)

    # Validate through Pydantic
    try:
        return CairnConfig.model_validate(merged)
    except Exception as exc:
        raise ConfigError(f"Config validation failed: {exc}") from exc
