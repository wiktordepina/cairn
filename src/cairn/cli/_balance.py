"""`cairn balance` — show account balance across configured providers.

Iterates over every provider in ``[providers.*]``; calls each
adapter's ``balance()`` method in parallel; prints one row per
provider. Adapters with no public balance API
(Anthropic / OpenAI without admin key) print a "no public API"
stub row rather than being hidden — answers the user's implicit
"why isn't my anthropic balance shown?" without docs lookup.

Exit codes:

- 0 — every provider with an endpoint succeeded (or all configured
  providers lack public APIs)
- 2 — every provider with an endpoint failed
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

import typer

from cairn.cli._format import print_error
from cairn.config._loader import ConfigError, load_config
from cairn.config._secrets import SecretResolver
from cairn.providers._registry import ProviderNotConfiguredError, ProviderRegistry

if TYPE_CHECKING:
    from cairn.domain._provider import BalanceInfo

__all__ = ["register"]


class _Row(NamedTuple):
    provider: str
    info: BalanceInfo | None
    error: str | None


def register(app: typer.Typer) -> None:
    """Mount the ``cairn balance`` command on *app*."""
    app.command("balance", help="Show account balance for each configured provider.")(_balance)


def _balance(ctx: typer.Context) -> None:
    profile = ctx.obj.get("profile") if ctx.obj else None
    try:
        cfg = load_config(profile=profile)
    except ConfigError as exc:
        print_error(str(exc), hint="run `cairn config validate` for the standalone check.")
        raise typer.Exit(1) from None

    provider_names = sorted(cfg.providers.keys())
    if not provider_names:
        typer.echo("No providers configured.")
        return

    resolver = SecretResolver()
    registry = ProviderRegistry(cfg, resolver)

    rows = asyncio.run(_gather(registry, provider_names))
    _print_table(rows)

    endpoint_attempts = [r for r in rows if r.info is not None or r.error is not None]
    if endpoint_attempts and all(r.error is not None for r in endpoint_attempts):
        raise typer.Exit(2)


async def _gather(registry: ProviderRegistry, names: list[str]) -> list[_Row]:
    """Resolve and call ``balance()`` on each provider concurrently."""

    async def one(name: str) -> _Row:
        try:
            provider = registry.by_name(name)
        except ProviderNotConfiguredError as exc:
            return _Row(provider=name, info=None, error=str(exc))
        try:
            info = await provider.balance()
        except Exception as exc:  # noqa: BLE001 — surface the message; never crash the CLI
            return _Row(provider=name, info=None, error=type(exc).__name__)
        return _Row(provider=name, info=info, error=None)

    return list(await asyncio.gather(*(one(n) for n in names)))


def _print_table(rows: list[_Row]) -> None:
    """Render the rows as a simple aligned table.

    Layout matches the design doc §3.2 example: ``provider``,
    ``remaining``, ``used``, ``source``. Endpoint-less providers print
    ``—`` in the numeric columns and ``no public API`` in the source
    column. Endpoint failures print ``error: <type>`` in the
    ``remaining`` column.
    """
    headers = ("provider", "remaining", "used", "source")
    formatted = [_row_cells(r) for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in formatted)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    typer.echo(line)
    for cells in formatted:
        typer.echo("  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)))


def _row_cells(row: _Row) -> tuple[str, str, str, str]:
    if row.error is not None:
        return (row.provider, f"error: {row.error}", "—", "—")
    if row.info is None:
        return (row.provider, "—", "—", "no public API")
    info = row.info
    return (
        row.provider,
        _money(info.currency, info.remaining),
        _money(info.currency, info.used),
        info.source,
    )


def _money(currency: str, value: float) -> str:
    """Format a currency amount with two decimal places."""
    return f"{currency} {value:.2f}" if currency else f"{value:.2f}"
