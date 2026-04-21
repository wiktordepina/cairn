"""Schema migration infrastructure for cairn configuration.

Migrations are registered via the `@migration` decorator and applied
in chain order by `migrate()`. No migrations exist yet (schema_version=1),
but the infrastructure is ready for when fields move or rename.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cairn.config._models import CURRENT_SCHEMA_VERSION

type _Transform = Callable[[dict[str, Any]], dict[str, Any]]

# Global registry: from_version -> (to_version, transform_fn)
_MIGRATIONS: dict[int, tuple[int, _Transform]] = {}


class MigrationError(Exception):
    """Raised when a migration chain is broken or a migration fails."""


def migration(*, from_version: int, to_version: int) -> Callable[[_Transform], _Transform]:
    """Decorator to register a config migration from one version to the next."""

    def decorator(fn: _Transform) -> _Transform:
        if from_version in _MIGRATIONS:
            raise MigrationError(f"Duplicate migration registered for from_version={from_version}")
        _MIGRATIONS[from_version] = (to_version, fn)
        return fn

    return decorator


def migrate(
    raw: dict[str, Any],
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Apply all registered migrations to bring *raw* to *target_version*.

    Returns the dict unmodified if already at the target version.
    Raises `MigrationError` if a gap exists in the migration chain.
    """
    current = raw.get("schema_version")
    if current is None:
        raise MigrationError("Config has no schema_version field")

    if current == target_version:
        return raw

    if current > target_version:
        raise MigrationError(
            f"Config version {current} is newer than target {target_version} — cannot downgrade"
        )

    result = dict(raw)
    version = current
    while version < target_version:
        if version not in _MIGRATIONS:
            raise MigrationError(
                f"No migration registered for version {version} -> {version + 1}. "
                f"Chain is broken at version {version}."
            )
        next_version, transform = _MIGRATIONS[version]
        result = transform(result)
        if result.get("schema_version") != next_version:
            raise MigrationError(
                f"Migration {version} -> {next_version} did not update schema_version"
            )
        version = next_version

    return result
