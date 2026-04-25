"""Activity indicator — small spinner + label in the header.

Surfaces three "things are happening" signals the observer already
knows about: the model is composing a reply ("thinking"), a reply is
streaming tokens, or a tool call is running. The indicator holds no
orchestrator state of its own — `SessionScreen` flips it between
states as events arrive.
"""

from __future__ import annotations

from typing import Literal

from textual.widgets import Label

ActivityState = Literal["idle", "thinking", "streaming", "tool"]

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TICK_INTERVAL = 0.1


class ActivityIndicator(Label):
    """Spinner + short label for the current turn-lifecycle state.

    Idle renders empty; the other three states render a rotating
    braille glyph plus a short description. The ticker runs only
    while the indicator is non-idle — we stop the interval in
    `_set_state` and restart it when an active state is entered.
    """

    DEFAULT_CSS = """
    ActivityIndicator {
        height: 1;
        padding: 0 2;
        margin: 0 1;
        color: $accent;
        text-style: italic;
    }
    ActivityIndicator.-idle {
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._state: ActivityState = "idle"
        self._label: str = ""
        self._frame_idx: int = 0
        self._timer: object | None = None  # Textual Timer handle
        self.add_class("-idle")

    # -- Public API -----------------------------------------------------

    @property
    def state(self) -> ActivityState:
        return self._state

    @property
    def label(self) -> str:
        return self._label

    def set_idle(self) -> None:
        self._set_state("idle", "")

    def set_thinking(self) -> None:
        self._set_state("thinking", "thinking")

    def set_streaming(self) -> None:
        self._set_state("streaming", "streaming")

    def set_tool(self, tool_name: str) -> None:
        self._set_state("tool", tool_name)

    # -- Internals ------------------------------------------------------

    def _set_state(self, state: ActivityState, label: str) -> None:
        self._state = state
        self._label = label
        self._frame_idx = 0
        self.set_class(state == "idle", "-idle")
        if state == "idle":
            self._stop_timer()
            self.update("")
            return
        self._start_timer()
        self._render_current_frame()

    def _start_timer(self) -> None:
        if self._timer is not None:
            return
        # The interval is bound to the running Textual event loop, so
        # skip it when the widget isn't mounted yet (happens in unit
        # tests that drive state transitions without the pilot).
        if not self.is_mounted:
            return
        self._timer = self.set_interval(_TICK_INTERVAL, self._tick)

    def _stop_timer(self) -> None:
        timer = self._timer
        if timer is None:
            return
        self._timer = None
        stop = getattr(timer, "stop", None)
        if callable(stop):
            stop()

    def _tick(self) -> None:
        if self._state == "idle":
            return
        self._frame_idx = (self._frame_idx + 1) % len(_FRAMES)
        self._render_current_frame()

    def _render_current_frame(self) -> None:
        glyph = _FRAMES[self._frame_idx]
        self.update(f"{glyph} {self._label}" if self._label else glyph)
