"""`cairn config secret` subcommands.

Three commands wrapping the OS keychain via `keyring`:

- `set <ref>` writes a secret. Only `keyring:<service>:<key>`
  references are accepted; other schemes (`env:`, `prompt:`,
  `literal:`) are rejected with a remediation hint. Existing entries
  prompt for overwrite confirmation (default no) so accidental
  rotation is a deliberate keypress, not a silent clobber.
- `list` walks the loaded `CairnConfig`, collects every `SecretRef`,
  and probes its backing store read-only.
- `delete <ref>` removes a keyring entry after a y/N confirmation.

All three exit 4 when the keyring backend is unavailable so scripts
can distinguish that recoverable state from generic failures.
"""

from __future__ import annotations

import getpass

import typer

from cairn.cli._format import collect_secret_refs, print_error, secret_ref_annotation
from cairn.config._loader import ConfigError, load_config
from cairn.config._models import SecretRef

__all__ = ["register"]


_KEYRING_HINT = (
    "on Linux this usually means the gnome-keyring or kwallet daemon "
    "isn't running; on macOS check Keychain Access; on Windows check "
    "Credential Manager."
)


def register(app: typer.Typer) -> None:
    """Mount the secret subcommands on *app*."""
    app.command("set", help="Store a secret value in the OS keychain.")(_set)
    app.command("list", help="List secret references referenced by this config.")(_list)
    app.command("delete", help="Remove a secret value from the OS keychain.")(_delete)


# ---------------------------------------------------------------------------
# `cairn config secret set`
# ---------------------------------------------------------------------------


def _set(
    ref: str = typer.Argument(..., help="Secret reference, e.g. keyring:cairn:anthropic-api-key"),
) -> None:
    parsed = _parse_ref_or_exit(ref)
    if parsed.scheme != "keyring":
        print_error(
            f"{ref!r} is a {parsed.scheme!r} reference; nothing to set in the keyring.",
            hint=_remediation_for(parsed),
        )
        raise typer.Exit(1)

    import keyring
    from keyring.errors import KeyringError

    service, key = parsed.params

    try:
        existing = keyring.get_password(service, key)
    except (KeyringError, RuntimeError) as exc:
        print_error(f"keyring backend unavailable: {exc}", hint=_KEYRING_HINT)
        raise typer.Exit(4) from None

    if existing is not None and not typer.confirm(
        f"An entry already exists for service={service}, key={key}. Overwrite?",
        default=False,
    ):
        typer.echo("Aborted; keyring entry left unchanged.")
        return

    try:
        value = getpass.getpass(f"Value for {ref} (input hidden): ")
    except (KeyboardInterrupt, EOFError):
        typer.echo("\nAborted.", err=True)
        raise typer.Exit(1) from None

    if not value:
        print_error("empty value; refusing to store.")
        raise typer.Exit(1)

    try:
        keyring.set_password(service, key, value)
    except (KeyringError, RuntimeError) as exc:
        print_error(f"keyring backend unavailable: {exc}", hint=_KEYRING_HINT)
        raise typer.Exit(4) from None

    typer.echo(f"✓ Stored in keyring (service={service}, key={key}).")


# ---------------------------------------------------------------------------
# `cairn config secret list`
# ---------------------------------------------------------------------------


def _list(ctx: typer.Context) -> None:
    profile = _profile_from_ctx(ctx)
    try:
        cfg = load_config(profile=profile)
    except ConfigError as exc:
        print_error(str(exc), hint="run `cairn config validate` for the standalone check.")
        raise typer.Exit(1) from None

    refs = collect_secret_refs(cfg)
    if not refs:
        typer.echo("(no secret references in this config)")
        return

    rows: list[tuple[str, str, str]] = []
    for path, ref in refs:
        ref_str = f"{ref.scheme}:{':'.join(ref.params)}"
        annotation = secret_ref_annotation(ref) or "literal value (stored in config)"
        # Strip the leading arrow we use for inline TOML annotation;
        # in this list context it'd be visual noise.
        if annotation.startswith("→ "):
            annotation = annotation[2:]
        rows.append((ref_str, annotation, ".".join(path)))

    width = max(len(r) for r, _, _ in rows)
    for ref_str, status, path in rows:
        typer.echo(f"{ref_str.ljust(width)}   {status}   ({path})")


# ---------------------------------------------------------------------------
# `cairn config secret delete`
# ---------------------------------------------------------------------------


def _delete(
    ref: str = typer.Argument(..., help="Secret reference to remove from the keyring."),
) -> None:
    parsed = _parse_ref_or_exit(ref)
    if parsed.scheme != "keyring":
        print_error(
            f"{ref!r} is a {parsed.scheme!r} reference; nothing to delete from the keyring.",
            hint=_remediation_for(parsed),
        )
        raise typer.Exit(1)

    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError

    service, key = parsed.params

    if not typer.confirm(
        f"Delete keyring entry service={service}, key={key}?",
        default=False,
    ):
        typer.echo("Aborted.")
        return

    try:
        keyring.delete_password(service, key)
    except PasswordDeleteError:
        print_error(f"no keyring entry for service={service}, key={key}.")
        raise typer.Exit(1) from None
    except (KeyringError, RuntimeError) as exc:
        print_error(f"keyring backend unavailable: {exc}", hint=_KEYRING_HINT)
        raise typer.Exit(4) from None

    typer.echo("✓ Removed.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ref_or_exit(raw: str) -> SecretRef:
    try:
        return SecretRef.parse(raw)
    except ValueError:
        print_error(
            f"invalid secret reference: {raw!r}",
            hint="expected `keyring:<service>:<key>` (or `env:VAR`, `prompt:[message]`).",
        )
        raise typer.Exit(1) from None


def _remediation_for(ref: SecretRef) -> str:
    match ref.scheme:
        case "env":
            return f"set the {ref.params[0]} environment variable in your shell."
        case "prompt":
            return "prompt: references resolve interactively at use time; no setup needed."
        case "literal":
            return "literal: references are stored verbatim in the config file."
        case _:
            return "use a keyring: reference."


def _profile_from_ctx(ctx: typer.Context) -> str | None:
    if ctx.obj is None:
        return None
    return ctx.obj.get("profile")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAny]
