"""`cairn config` subcommands.

Read-only / no-side-effect commands first (`show`, `paths`,
`validate`), followed by the schema-migration runner (`migrate`)
which is read-only by default and writes only with `--write`. Init /
secret subcommands ship in subsequent commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomli_w
import typer

from cairn.cli._format import print_error, render_config_toml
from cairn.config._loader import (
    ConfigError,
    config_paths,
    discover_project_root,
    load_config,
    load_raw,
    user_config_path,
)
from cairn.config._migrations import MigrationError, migrate
from cairn.config._models import CURRENT_SCHEMA_VERSION

if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Mount the read-only config subcommands on *app*."""
    app.command("show", help="Print the merged config (secrets redacted).")(_show)
    app.command("paths", help="List config file paths in resolution order.")(_paths)
    app.command("validate", help="Validate every layer; exit 1 on failure.")(_validate)
    app.command("migrate", help="Migrate config files to the current schema.")(_migrate)


# ---------------------------------------------------------------------------
# `cairn config show`
# ---------------------------------------------------------------------------


def _show(ctx: typer.Context) -> None:
    profile = _profile_from_ctx(ctx)
    try:
        cfg = load_config(profile=profile)
    except ConfigError as exc:
        print_error(str(exc), hint="run `cairn config validate` for the standalone check.")
        raise typer.Exit(1) from None
    layers = [(name, path) for name, path in config_paths() if path.is_file()]
    typer.echo(render_config_toml(cfg, layer_paths=layers), nl=False)


# ---------------------------------------------------------------------------
# `cairn config paths`
# ---------------------------------------------------------------------------


def _paths(ctx: typer.Context) -> None:
    _ = _profile_from_ctx(ctx)  # accepted for symmetry; unused here
    layers = _all_layer_paths()
    width = max(len(name) for name, _, _ in layers)
    path_width = max(len(str(path)) for _, path, _ in layers)
    for name, path, loaded in layers:
        status = "loaded" if loaded else "not present"
        typer.echo(f"{name.ljust(width)}  {str(path).ljust(path_width)}  ({status})")


def _all_layer_paths() -> list[tuple[str, Path, bool]]:
    """Return `[(layer, path, is_loaded), …]` for every potential layer.

    Differs from `config.config_paths()` by also including project /
    local entries that don't (yet) exist on disk — the CLI surface
    wants to make missing overrides visible rather than silently
    dropping them.
    """
    out: list[tuple[str, Path, bool]] = []
    user_p = user_config_path()
    out.append(("user", user_p, user_p.is_file()))
    root = discover_project_root()
    if root is not None:
        proj = root / ".cairn" / "config.toml"
        out.append(("project", proj, proj.is_file()))
        local = root / ".cairn" / "config.local.toml"
        out.append(("local", local, local.is_file()))
    return out


# ---------------------------------------------------------------------------
# `cairn config validate`
# ---------------------------------------------------------------------------


def _validate(ctx: typer.Context) -> None:
    profile = _profile_from_ctx(ctx)
    try:
        load_config(profile=profile)
    except ConfigError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None
    # Silent on success — `&& echo OK` is the documented idiom.


# ---------------------------------------------------------------------------
# `cairn config migrate`
# ---------------------------------------------------------------------------


def _migrate(
    ctx: typer.Context,
    write: bool = typer.Option(  # noqa: B008 — Typer Option default
        False,
        "--write",
        help="Apply migrations in place (atomic). Defaults to dry-run.",
    ),
) -> None:
    _ = _profile_from_ctx(ctx)  # accepted for symmetry; unused here
    layers = _all_layer_paths()
    loaded = [(name, path) for name, path, present in layers if present]
    if not loaded:
        typer.echo("(no config files discovered — nothing to migrate)")
        return

    name_width = max(len(name) for name, _ in loaded)
    any_changed = False
    for name, path in loaded:
        try:
            raw = load_raw(path)
        except ConfigError as exc:
            print_error(f"{path}: {exc}")
            raise typer.Exit(1) from None
        current_version = raw.get("schema_version")
        if current_version == CURRENT_SCHEMA_VERSION:
            typer.echo(
                f"{name.ljust(name_width)}  {path}  "
                f"schema_version={current_version} (current — no action)"
            )
            continue
        try:
            migrated = migrate(raw, target_version=CURRENT_SCHEMA_VERSION)
        except MigrationError as exc:
            print_error(f"{path}: {exc}")
            raise typer.Exit(1) from None
        any_changed = True
        target = migrated.get("schema_version")
        verb = "would migrate" if not write else "migrated"
        typer.echo(
            f"{name.ljust(name_width)}  {path}  "
            f"schema_version={current_version} → {target} ({verb})"
        )
        if write:
            _write_migrated(path, migrated, current_version)

    if not any_changed:
        typer.echo("nothing to migrate.")


def _write_migrated(path: Path, migrated: dict[str, object], from_version: object) -> None:
    """Atomically rewrite *path* with *migrated*; back up the original.

    Backup lands at `<path>.pre-migrate-<from_version>` so the user
    can restore if a bad migration ever lands.
    """
    backup = path.with_name(f"{path.name}.pre-migrate-{from_version}")
    backup.write_bytes(path.read_bytes())
    body = tomli_w.dumps(migrated)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_from_ctx(ctx: typer.Context) -> str | None:
    """Pull the `--profile` value stashed by the root callback."""
    if ctx.obj is None:
        return None
    return ctx.obj.get("profile")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAny]
