"""`cairn trust` subcommands.

Thin operator surface over `cairn.conventions.AllowlistStore`. The
primary trust-decision UX in everyday use is
`TextualPromptTrustGate` (UI brick, 0.10.0); these commands are the
non-interactive / scripting alternative — useful for pre-trusting a
project before it's cloned, batch setup, or removing entries
without launching the UI.
"""

from __future__ import annotations

from pathlib import Path

import typer

from cairn.cli._format import print_error
from cairn.conventions._trust import AllowlistStore, default_allowlist_path

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Mount the trust subcommands on *app*."""
    app.command("add", help="Add a project path to the trust allowlist.")(_add)
    app.command("list", help="List trusted project paths.")(_list)
    app.command("remove", help="Remove a project path from the trust allowlist.")(_remove)


def _store() -> AllowlistStore:
    return AllowlistStore(default_allowlist_path())


# ---------------------------------------------------------------------------
# `cairn trust add`
# ---------------------------------------------------------------------------


def _add(
    path: Path = typer.Argument(  # noqa: B008
        None,
        help="Project root to trust (defaults to current working directory).",
    ),
) -> None:
    target = (path or Path.cwd()).resolve()
    store = _store()
    already = any(entry.path == target for entry in store.list_trusted())
    store.add(target)
    if already:
        typer.echo(f"already trusted: {target}")
    else:
        typer.echo(f"✓ Added {target}.")


# ---------------------------------------------------------------------------
# `cairn trust list`
# ---------------------------------------------------------------------------


def _list() -> None:
    entries = _store().list_trusted()
    if not entries:
        typer.echo("(no trusted projects)")
        return
    width = max(len(str(entry.path)) for entry in entries)
    for entry in entries:
        typer.echo(f"{str(entry.path).ljust(width)}  (added {entry.added_at.isoformat()})")


# ---------------------------------------------------------------------------
# `cairn trust remove`
# ---------------------------------------------------------------------------


def _remove(
    path: Path = typer.Argument(  # noqa: B008
        None,
        help="Project root to remove (defaults to current working directory).",
    ),
) -> None:
    target = (path or Path.cwd()).resolve()
    if not _store().remove(target):
        print_error(f"not in allowlist: {target}")
        raise typer.Exit(1)
    typer.echo(f"✓ Removed {target}.")
