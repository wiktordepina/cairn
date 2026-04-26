"""Resolve a model id to its user-facing display name.

Every UI surface that holds a bare model id (the session header, the
``/model`` swap banners, the reload-revert message, the ``/profile``
report) renders ``display_name`` instead of the id. The id stays the
canonical identifier in persistence, logs, and the orchestrator API —
only rendered text changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.config import ModelRegistry


def resolve_label(registry: ModelRegistry | None, model_id: str) -> str:
    """Return the model's `display_name`, or fall back to *model_id*.

    Falls back when:

    - *registry* is None (test harnesses with mock orchestrators);
    - the registry has no entry for *model_id* (the user dropped the
      model from config mid-session — keep the bare id rather than
      raising, so the UI continues to render).
    """
    if registry is None:
        return model_id
    from cairn.config._registry import ModelNotFoundError

    try:
        return registry.by_id(model_id).display_name
    except ModelNotFoundError:
        return model_id


__all__ = ["resolve_label"]
