"""`cairn config init` and the stub-file content constants.

The init flow elicits a companion name and a primary provider, then
writes:

- `<user_config_dir>/config.toml` — a minimal valid `CairnConfig`.
- `<user_config_dir>/soul_document.md` — companion identity stub.
- `<user_config_dir>/user_context.md` — user-context stub.
- `<user_config_dir>/MEMORY.md` — empty curated-memory stub.

Each Markdown stub is written only when the target path doesn't
already exist, so re-running `init` after a partial setup never
trashes hand-edits. The TOML file refuses to clobber wholesale —
that's the loud "you already have a config" exit-1 path.
"""

from __future__ import annotations

import getpass
from typing import TYPE_CHECKING

import keyring
import tomli_w
import typer
from keyring.errors import KeyringError

from cairn.cli._format import print_error
from cairn.config._loader import user_config_dir, user_config_path

if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["register"]


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------


_PROVIDER_PRESETS: dict[str, dict[str, object]] = {
    "anthropic": {
        "default_model_id": "claude-opus-4-7",
        "display_name": "Claude Opus 4.7",
        "context_window": 200_000,
        "max_output_tokens": 32_000,
        "supports_tools": True,
        "supports_vision": True,
        "supports_thinking": True,
        "supports_prompt_cache": True,
        "input_cost_per_1m": 15.0,
        "output_cost_per_1m": 75.0,
        "cache_read_cost_per_1m": 1.5,
        "cache_write_cost_per_1m": 18.75,
    },
    "openai": {
        "default_model_id": "gpt-4o",
        "display_name": "GPT-4o",
        "context_window": 128_000,
        "max_output_tokens": 16_000,
        "supports_tools": True,
        "supports_vision": True,
        "supports_thinking": False,
        "supports_prompt_cache": True,
        "input_cost_per_1m": 2.5,
        "output_cost_per_1m": 10.0,
    },
    "openrouter": {
        "default_model_id": "anthropic/claude-opus-4-7",
        "display_name": "Claude Opus 4.7 (via OpenRouter)",
        "context_window": 200_000,
        "max_output_tokens": 32_000,
        "supports_tools": True,
        "supports_vision": True,
        "supports_thinking": True,
        "supports_prompt_cache": True,
        "input_cost_per_1m": 15.0,
        "output_cost_per_1m": 75.0,
    },
    "deepseek": {
        "default_model_id": "deepseek-chat",
        "display_name": "DeepSeek Chat",
        "context_window": 64_000,
        "max_output_tokens": 8_000,
        "supports_tools": True,
        "supports_vision": False,
        "supports_thinking": False,
        "supports_prompt_cache": True,
        "input_cost_per_1m": 0.27,
        "output_cost_per_1m": 1.10,
    },
}


# ---------------------------------------------------------------------------
# Stub file contents
# ---------------------------------------------------------------------------


_SOUL_DOCUMENT_STUB = """\
# {name} — Soul Document

Short identity statement for your companion. Edit this freely.

## Who I am

A coding companion built around continuity, memory, and genuine
collaboration. Replace this paragraph with how you want {name} to
think of itself.

## How I engage

- Concise by default; longer when the problem warrants it.
- Pushes back on approaches it disagrees with, with reasons.
- Acknowledges uncertainty rather than guessing.

## Communication style

British English by default. Match the user's tone — code review
style for code review, conversational for design chats.
"""


_USER_CONTEXT_STUB = """\
# User Context

Short stable facts about you that {name} should keep in mind. Edit
freely.

## Identity

- Name:
- Role:
- Pronouns:

## Stack and tooling

- Primary languages:
- Frameworks / runtimes:
- Editors / OS:

## Preferences

- Communication style:
- What to avoid:
- What works well:
"""


_MEMORY_STUB = """\
# Memory

Curated, high-confidence facts {name} has learned. Edit by hand for
now (Tier-3 auto-curation lands in V2).
"""


# ---------------------------------------------------------------------------
# `cairn config init`
# ---------------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Mount the init subcommand on *app*."""
    app.command("init", help="Create a minimal user config (first-run setup).")(_init)


def _init(
    ctx: typer.Context,
    no_prompt: bool = typer.Option(  # noqa: B008
        False,
        "--no-prompt",
        help="Skip elicitation; write defaults verbatim. Useful for scripted setup.",
    ),
) -> None:
    target = user_config_path()
    if target.exists():
        print_error(
            f"{target} already exists; refusing to overwrite.",
            hint="edit the file by hand, or move it aside and re-run.",
        )
        raise typer.Exit(1)

    profile_name = _profile_from_ctx(ctx) or "companion"

    if no_prompt:
        companion_name = "Cairn"
        provider = "anthropic"
        store_key = False
    else:
        typer.echo("Cairn first-run setup.\n")
        companion_name = typer.prompt("Companion name", default="Cairn")
        provider = _prompt_provider()
        store_key = typer.confirm("Store API key in the OS keychain now?", default=False)

    config_body = _build_config_toml(
        companion_name=companion_name,
        provider=provider,
        profile_name=profile_name,
        config_dir=user_config_dir(),
    )

    user_config_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(config_body, encoding="utf-8")
    typer.echo(f"✓ Wrote {target}.")

    _write_stubs(user_config_dir(), companion_name)

    if store_key:
        _store_provider_key(provider)

    typer.echo("\nRun `cairn` to start a session.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_from_ctx(ctx: typer.Context) -> str | None:
    if ctx.obj is None:
        return None
    return ctx.obj.get("profile")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAny]


def _prompt_provider() -> str:
    typer.echo("Primary provider:")
    options = list(_PROVIDER_PRESETS)
    for idx, name in enumerate(options, start=1):
        typer.echo(f"  {idx}. {name}")
    while True:
        raw = typer.prompt("Choose", default="1")
        try:
            idx = int(raw)
        except ValueError:
            typer.echo("(enter a number)")
            continue
        if 1 <= idx <= len(options):
            return options[idx - 1]
        typer.echo(f"(choose 1..{len(options)})")


def _build_config_toml(
    *,
    companion_name: str,
    provider: str,
    profile_name: str,
    config_dir: Path,
) -> str:
    preset = _PROVIDER_PRESETS[provider]
    model_id = str(preset["default_model_id"])
    keyring_ref = f"keyring:cairn:{provider}-api-key"

    raw: dict[str, object] = {
        "schema_version": 1,
        "active_profile": profile_name,
        "providers": {provider: {"api_key": keyring_ref}},
        "models": [
            {
                "id": model_id,
                "provider": provider,
                "display_name": preset["display_name"],
                "context_window": preset["context_window"],
                "max_output_tokens": preset["max_output_tokens"],
                "supports_tools": preset["supports_tools"],
                "supports_vision": preset["supports_vision"],
                "supports_thinking": preset["supports_thinking"],
                "supports_prompt_cache": preset["supports_prompt_cache"],
                "input_cost_per_1m": preset["input_cost_per_1m"],
                "output_cost_per_1m": preset["output_cost_per_1m"],
                "roles": ["primary", "utility"],
            }
        ],
        "profiles": {
            profile_name: {
                "name": companion_name,
                "soul_document_path": str(config_dir / "soul_document.md"),
                "user_context_path": str(config_dir / "user_context.md"),
                "memory_md_path": str(config_dir / "MEMORY.md"),
                "primary_model": model_id,
                "utility_model": model_id,
            }
        },
    }
    cache_read = preset.get("cache_read_cost_per_1m")
    cache_write = preset.get("cache_write_cost_per_1m")
    model_dict = raw["models"][0]  # type: ignore[index]
    if isinstance(model_dict, dict):
        if cache_read is not None:
            model_dict["cache_read_cost_per_1m"] = cache_read
        if cache_write is not None:
            model_dict["cache_write_cost_per_1m"] = cache_write
    return tomli_w.dumps(raw)


def _write_stubs(config_dir: Path, companion_name: str) -> None:
    """Write the three Markdown stubs, skipping any that already exist."""
    stubs: list[tuple[str, str]] = [
        ("soul_document.md", _SOUL_DOCUMENT_STUB.format(name=companion_name)),
        ("user_context.md", _USER_CONTEXT_STUB.format(name=companion_name)),
        ("MEMORY.md", _MEMORY_STUB.format(name=companion_name)),
    ]
    for filename, body in stubs:
        path = config_dir / filename
        if path.exists():
            typer.echo(f"  (kept existing {path})")
            continue
        path.write_text(body, encoding="utf-8")
        typer.echo(f"✓ Wrote stub {path}.")


def _store_provider_key(provider: str) -> None:
    service = "cairn"
    key = f"{provider}-api-key"
    try:
        value = getpass.getpass(f"API key for {provider} (input hidden): ")
    except (KeyboardInterrupt, EOFError):
        typer.echo("\n(skipped — no key stored)", err=True)
        return
    if not value:
        typer.echo("(empty input — no key stored)")
        return
    try:
        keyring.set_password(service, key, value)
    except (KeyringError, RuntimeError) as exc:
        print_error(f"keyring backend unavailable: {exc}")
        return
    typer.echo(f"✓ Stored in keyring (service={service}, key={key}).")
