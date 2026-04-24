"""Cairn's console-script entry point.

Tranche 1 keeps this deliberately small: parse `--profile` and the
standard `--help`/`--version` flags, bootstrap logging + SSL, load
config, build the orchestrator, and launch the Textual app. The full
`cairn config show/paths/validate/migrate/secret/diff` surface
stays deferred to the dedicated CLI brick.

Prior art the arch-doc §4.1 "CLI summary" section sets out:
eventually `cairn [--profile NAME]` co-exists with `cairn config …`,
`cairn trust …`, and the like. For now we only need to launch the
app.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from typing import TYPE_CHECKING

from cairn.logging import setup_logging
from cairn.ssl import setup_ssl

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cairn` console script.

    Returns a process exit code. Split from the Textual launch path
    so tests can drive argument parsing without spinning up the
    terminal.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    setup_logging()
    setup_ssl()

    # Deferred import: builds the orchestrator + Textual app. Keeping
    # it out of `main`'s module scope means `--help` / `--version`
    # paths stay cheap and don't pay the `textual` import cost.
    from cairn.ui._bootstrap import launch

    return launch(profile_name=args.profile)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cairn",
        description="Launch the Cairn companion UI.",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        default=None,
        help="Use this profile (overrides the config resolution order).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cairn {_read_version()}",
    )
    return parser


def _read_version() -> str:
    try:
        return importlib.metadata.version("cairn")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover — editable install
        return "0.0.0-dev"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
