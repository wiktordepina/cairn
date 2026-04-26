"""Cairn's console-script entry point.

The CLI surface is built on Typer (see ADR 0041). This module wires
up the root app, mounts sub-apps for `config` and `trust`, and
exposes the `main()` callable that `pyproject.toml`'s
`[project.scripts] cairn = "cairn.cli:main"` points at.

Sub-apps are registered by submodules under `cairn.cli.*` via their
`register(app)` callables. The skeleton ships with `--profile`,
`--version`, and the default no-subcommand UI launch path; the
config / trust / secret subcommands land in subsequent commits.
"""

from __future__ import annotations

import importlib.metadata
import sys
from typing import TYPE_CHECKING

import click
import typer

from cairn.logging import setup_logging
from cairn.ssl import setup_ssl

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["app", "main"]


# ---------------------------------------------------------------------------
# Root Typer app
# ---------------------------------------------------------------------------


app: typer.Typer = typer.Typer(
    name="cairn",
    help="Cairn companion CLI.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    add_completion=False,
)

config_app: typer.Typer = typer.Typer(
    help="Inspect and manage cairn configuration.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(config_app, name="config")

secret_app: typer.Typer = typer.Typer(
    help="Manage secrets in the OS keychain.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
config_app.add_typer(secret_app, name="secret")

trust_app: typer.Typer = typer.Typer(
    help="Manage the project trust allowlist.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(trust_app, name="trust")

# Late imports keep the dependency direction explicit: subcommand
# modules pull from `cairn.config` / `cairn.conventions`; this
# module just registers them.
from cairn.cli import _balance, _config, _init, _secrets, _trust  # noqa: E402

_config.register(config_app)
_init.register(config_app)
_secrets.register(secret_app)
_trust.register(trust_app)
_balance.register(app)


def _read_version() -> str:
    try:
        return importlib.metadata.version("cairn")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover — editable install
        return "0.0.0-dev"


def _version_callback(value: bool) -> None:  # noqa: FBT001 — Typer flag callback
    if not value:
        return
    typer.echo(f"cairn {_read_version()}")
    raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def _root(  # pyright: ignore[reportUnusedFunction]  # registered by decorator
    ctx: typer.Context,
    profile: str | None = typer.Option(
        None,
        "--profile",
        metavar="NAME",
        help="Use this profile (overrides the config resolution order).",
    ),
    _version: bool = typer.Option(
        False,
        "--version",
        is_eager=True,
        callback=_version_callback,
        help="Print the cairn version and exit.",
    ),
) -> int | None:
    """Root callback. Stashes shared options on `ctx.obj`; if no
    subcommand was given, launches the Textual UI."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile

    if ctx.invoked_subcommand is not None:
        return None

    if not _has_loadable_config():
        from cairn.config._loader import user_config_path

        typer.echo("No cairn configuration found.")
        typer.echo(f"  Run `cairn config init` to create one ({user_config_path()}).")
        return 0

    setup_logging()
    setup_ssl()

    # Deferred import: the bootstrap pulls in Textual + the full
    # collaborator graph. Keeping it out of the module scope means
    # `--help` / `--version` / subcommand paths stay cheap.
    from cairn.ui._bootstrap import launch

    return launch(profile_name=profile)


def _has_loadable_config() -> bool:
    """True iff *any* config layer exists for the current cwd / home.

    Used by the default-launch short-circuit: with no user config and
    no project config, there's nothing to bootstrap, so we redirect
    the user to `cairn config init` instead of raising `ConfigError`
    out of the Textual launch path.
    """
    from cairn.config._loader import config_paths

    return any(path.is_file() for _, path in config_paths())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cairn` console script.

    Returns a process exit code. Translates Typer / Click control-flow
    exceptions into integer exit codes so the entry point has a
    deterministic int contract regardless of which command path runs.
    """
    try:
        result = app(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code or 2
    except click.exceptions.Exit as exc:
        return int(exc.exit_code or 0)
    except click.exceptions.Abort:
        typer.echo("Aborted.", err=True)
        return 1
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
