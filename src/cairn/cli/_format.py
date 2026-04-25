"""Shared output formatting for the `cairn` CLI.

Two responsibilities live here:

- Round-tripping a `CairnConfig` back to TOML via `tomli_w`, with a
  comment header naming the layers that were merged and per-line
  annotations on `SecretRef` values noting whether the backing store
  has the secret in question.
- A tiny `print_error` helper so every subcommand surfaces failures
  in the same `cairn: <msg>\\n  hint: <hint>` shape on stderr.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import tomli_w
import typer

from cairn.config._models import SecretRef

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.config._models import CairnConfig


__all__ = [
    "collect_secret_refs",
    "print_error",
    "render_config_toml",
    "secret_ref_annotation",
]


# ---------------------------------------------------------------------------
# Secret-ref discovery + annotation
# ---------------------------------------------------------------------------


def collect_secret_refs(cfg: CairnConfig) -> list[tuple[tuple[str, ...], SecretRef]]:
    """Walk *cfg* and return `[(toml_path, ref), …]` for every `SecretRef`.

    `toml_path` is the dotted-table path tuple (e.g.
    `("providers", "anthropic", "api_key")`) so the renderer can match
    the produced TOML lines against the right secrets.
    """
    out: list[tuple[tuple[str, ...], SecretRef]] = []
    for provider_name, provider in cfg.providers.items():
        if isinstance(provider.api_key, SecretRef):
            out.append((("providers", provider_name, "api_key"), provider.api_key))
    return out


def secret_ref_annotation(ref: SecretRef) -> str | None:
    """Return a human-readable annotation for *ref*, or `None`.

    `keyring:` and `env:` annotations probe their respective backing
    stores read-only — they never resolve `prompt:` references and
    therefore never trigger a hidden interactive prompt during a
    `cairn config show`. `literal:` returns `None` because the value
    is the reference itself.
    """
    match ref.scheme:
        case "keyring":
            try:
                import keyring as kr
            except ImportError:  # pragma: no cover — declared dep
                return "→ keyring backend unavailable"
            try:
                service, key = ref.params
                value = kr.get_password(service, key)
            except Exception:  # noqa: BLE001 — backend errors are opaque
                return "→ keyring backend unavailable"
            return "→ present in keyring" if value is not None else "→ MISSING"
        case "env":
            (name,) = ref.params
            return "→ set" if os.environ.get(name) is not None else "→ unset"
        case "prompt":
            return "→ prompts on first use"
        case "literal":
            return None
        case _:
            return None


# ---------------------------------------------------------------------------
# Layer header + TOML rendering
# ---------------------------------------------------------------------------


def _render_layer_header(layer_paths: list[tuple[str, Path]]) -> str:
    if not layer_paths:
        return ""
    width = max(len(layer) for layer, _ in layer_paths)
    lines = ["# merged from:"]
    for layer, path in layer_paths:
        lines.append(f"#   {layer.ljust(width)}  {path}")
    return "\n".join(lines) + "\n\n"


def render_config_toml(
    cfg: CairnConfig,
    *,
    layer_paths: list[tuple[str, Path]],
) -> str:
    """Render *cfg* as TOML with a layer-comment header + secret annotations.

    `cfg.model_dump(mode="json")` serialises `SecretRef` instances back
    to their reference strings (`scheme:params…`) — never their
    resolved values. `tomli_w.dumps` produces the body; we then
    annotate the lines bearing each known secret reference.
    """
    # `exclude_none=True` skips Optional fields that resolved to
    # None — `tomli_w` rejects None values, and they carry no
    # information for the operator anyway.
    body = tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True))
    annotated = _annotate_secret_lines(body, collect_secret_refs(cfg))
    return _render_layer_header(layer_paths) + annotated


def _annotate_secret_lines(
    toml_text: str,
    refs: list[tuple[tuple[str, ...], SecretRef]],
) -> str:
    """Append `  # → annotation` to lines holding a known SecretRef value.

    Walks the rendered TOML line-by-line, tracking the current table
    via `[a.b.c]` headers, and matches `(table, leaf_field)` pairs
    against the collected refs. This avoids the trap of two providers
    using identical `api_key = "…"` strings being annotated as the
    same entry.
    """
    if not refs:
        return toml_text

    by_table: dict[tuple[str, ...], dict[str, str]] = {}
    for path, ref in refs:
        annotation = secret_ref_annotation(ref)
        if annotation is None:
            continue
        table = path[:-1]
        leaf = path[-1]
        by_table.setdefault(table, {})[leaf] = annotation

    out: list[str] = []
    current_table: tuple[str, ...] = ()
    for line in toml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inner = stripped[1:-1]
            if inner.startswith("[") and inner.endswith("]"):
                inner = inner[1:-1]  # array-of-tables `[[x.y]]`
            current_table = tuple(inner.split("."))
            out.append(line)
            continue
        if "=" in line and current_table in by_table:
            field = line.split("=", 1)[0].strip()
            annotation = by_table[current_table].get(field)
            if annotation is not None:
                out.append(f"{line}  # {annotation}")
                continue
        out.append(line)
    suffix = "\n" if toml_text.endswith("\n") else ""
    return "\n".join(out) + suffix


# ---------------------------------------------------------------------------
# Error printing
# ---------------------------------------------------------------------------


def print_error(message: str, *, hint: str | None = None) -> None:
    """Write a uniform `cairn: <msg>\\n  hint: <hint>` block to stderr."""
    typer.echo(f"cairn: {message}", err=True)
    if hint is not None:
        typer.echo(f"  hint: {hint}", err=True)
