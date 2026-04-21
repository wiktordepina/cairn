"""Schema migration runner.

Migrations are numbered SQL files in `_sql/` named `NNNN_<slug>.sql`.
Each is applied in its own transaction; the `schema_migrations` row is
written by the runner (not the SQL file).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING, NamedTuple

from cairn.persistence._errors import MigrationError

if TYPE_CHECKING:
    import aiosqlite

_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class Migration(NamedTuple):
    version: int
    name: str
    sql: str


def discover_migrations() -> list[Migration]:
    """Return all migrations from the `_migrations/` package data directory.

    Validates filename pattern and gap-free monotonicity.
    """
    migrations: list[Migration] = []
    pkg = resources.files("cairn.persistence._sql")
    for entry in pkg.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        match = _FILENAME_RE.match(name)
        if match is None:
            raise MigrationError(
                f"Invalid migration filename {name!r}; expected pattern NNNN_<slug>.sql"
            )
        version = int(match.group(1))
        slug = match.group(2)
        sql = entry.read_text(encoding="utf-8")
        migrations.append(Migration(version=version, name=slug, sql=sql))

    migrations.sort(key=lambda m: m.version)

    # Validate no gaps starting from 1
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"Migration chain has a gap: expected version {expected}, "
                f"got {migration.version} ({migration.name})"
            )
    return migrations


async def applied_versions(conn: aiosqlite.Connection) -> set[int]:
    """Return the set of versions already applied to *conn*.

    Returns an empty set if the `schema_migrations` table does not exist.
    """
    try:
        cursor = await conn.execute("SELECT version FROM schema_migrations")
        rows = await cursor.fetchall()
        await cursor.close()
    except Exception as exc:
        # Table doesn't exist yet — treat as empty
        if "no such table" in str(exc).lower():
            return set()
        raise
    return {row[0] for row in rows}


async def apply_pending(conn: aiosqlite.Connection) -> list[int]:
    """Apply all unapplied migrations to *conn*.

    Returns the list of newly-applied version numbers.
    """
    migrations = discover_migrations()
    already = await applied_versions(conn)

    if migrations:
        max_disk = max(m.version for m in migrations)
        if already and max(already) > max_disk:
            raise MigrationError(
                f"Database has migration version {max(already)} applied, "
                f"but the highest available on disk is {max_disk}. "
                f"Refusing to start — DB is ahead of code."
            )

    applied: list[int] = []
    for migration in migrations:
        if migration.version in already:
            continue
        try:
            await conn.execute("BEGIN")
            await conn.executescript(migration.sql)
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, now),
            )
            await conn.commit()
        except Exception as exc:
            await conn.rollback()
            raise MigrationError(
                f"Migration {migration.version:04d}_{migration.name} failed: {exc}"
            ) from exc
        applied.append(migration.version)

    return applied
