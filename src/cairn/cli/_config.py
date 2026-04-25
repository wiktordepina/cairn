"""`cairn config` subcommands.

This commit lands the read-only pair: `show` and `paths`. Validate /
migrate / init / secret subcommands ship in subsequent commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from cairn.cli._format import print_error, render_config_toml
from cairn.config._loader import (
    ConfigError,
    config_paths,
    discover_project_root,
    load_config,
    user_config_path,
)

if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Mount the read-only config subcommands on *app*."""
    app.command("show", help="Print the merged config (secrets redacted).")(_show)
    app.command("paths", help="List config file paths in resolution order.")(_paths)


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
# Helpers
# ---------------------------------------------------------------------------


def _profile_from_ctx(ctx: typer.Context) -> str | None:
    """Pull the `--profile` value stashed by the root callback."""
    if ctx.obj is None:
        return None
    return ctx.obj.get("profile")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAny]
