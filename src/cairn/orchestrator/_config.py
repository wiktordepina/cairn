"""Orchestrator runtime config — caps, timeouts, defaults.

Distinct from `CairnConfig` (which is user-facing TOML). This is the
orchestrator's own knobs, populated at construction from profile config
plus sensible defaults.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OrchestratorConfig(BaseModel):
    """Runtime knobs for the orchestrator."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = Field(default=10, ge=1)
    """Hard cap on tool-dispatch iterations per turn."""

    max_model_behaviour_retries: int = Field(default=2, ge=0)
    """How many times to feed an error back to the model (malformed tool-call
    JSON, unknown tool name) before giving up. Does not count against
    `max_iterations`."""

    max_turn_duration_s: float = Field(default=600.0, gt=0)
    """Wall-clock cap on a single turn. Defaults to 10 minutes."""

    preparer_timeout_s: float = Field(default=30.0, gt=0)
    """Per-preparer timeout for the prepare middleware chain."""

    event_queue_size: int = Field(default=64, ge=1)
    """Bounded queue between the turn loop and the UI consumer. Full queue
    back-pressures the provider stream."""

    budget_warn_threshold_fraction: float = Field(default=0.8, gt=0, le=1)
    """Emit a `BudgetWarning` once session spend crosses this fraction
    of the session cap."""
