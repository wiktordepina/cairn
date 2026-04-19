"""Deep merge for raw config dicts — no Pydantic dependency."""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Rules:
    - Dicts merge recursively (keys in override add to or replace keys in base).
    - Lists replace (not concatenate) — the override list wins entirely.
    - Scalars replace.
    - ``None`` in override does **not** erase a base value — it is skipped.
    """
    merged: dict[str, Any] = {}

    for key in base.keys() | override.keys():
        if key in override:
            ov = override[key]
            if ov is None and key in base:
                # None in override does not erase base value
                merged[key] = base[key]
            elif key in base and isinstance(base[key], dict) and isinstance(ov, dict):
                merged[key] = deep_merge(
                    base[key],  # pyright: ignore[reportUnknownArgumentType]
                    ov,  # pyright: ignore[reportUnknownArgumentType]
                )
            else:
                merged[key] = ov
        else:
            merged[key] = base[key]

    return merged
