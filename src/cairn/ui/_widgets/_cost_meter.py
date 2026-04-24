"""Cost meter widget.

Shows the running session cost. Per resolved Q4, the meter updates
per turn (on `TurnComplete`) rather than per-event mid-stream —
`/cost` is the authoritative per-delegation / per-day breakdown.
"""

from __future__ import annotations

from textual.widgets import Label


class CostMeter(Label):
    """Simple running-total cost display.

    Tranche 1 ships as a text label; the `/cost` slash command
    (Tranche 2) is the fully-fledged breakdown UI. The meter is
    driven by the screen on `TurnComplete` — authoritative cost
    comes from the orchestrator's `CostTracker`.
    """

    DEFAULT_CSS = """
    CostMeter {
        padding: 0 1;
        color: $text-muted;
    }
    CostMeter.-warning {
        color: $warning;
    }
    """

    def __init__(self, *, precision: int = 6) -> None:
        self._precision = precision
        super().__init__(f"${0.0:.{precision}f}")
        self._cost_usd: float = 0.0

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    @property
    def precision(self) -> int:
        return self._precision

    def set_cost(self, cost_usd: float, *, warn: bool = False) -> None:
        """Set the running cost. `warn=True` applies the warning class."""
        self._cost_usd = cost_usd
        self.update(f"${cost_usd:.{self._precision}f}")
        self.set_class(warn, "-warning")
