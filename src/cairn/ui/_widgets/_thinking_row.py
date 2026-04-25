"""Collapsible thinking-block widget for the assistant transcript.

One `ThinkingRow` per contiguous thinking phase in an assistant
message. Mounted alongside `MessageView` in `ChatLog`. Default
state is collapsed — thinking blocks are long, low-signal, and
primarily useful when the model produces a surprising answer.
The header line stays visible so the user knows thinking
happened; pressing space (when focused) toggles the body.

Streaming model:
- First `ThinkingDelta` of a phase mounts a new `ThinkingRow`
  with `streaming=True`.
- Subsequent deltas are appended to the same row's buffer.
- The next non-thinking event (`AssistantTextDelta`,
  `ToolCallStarted`, `AssistantMessageComplete`) seals the row
  by calling `seal()`.

The screen owns the "currently-streaming row" handle in
`_pending_thinking_row` and clears it when sealing.
"""

from __future__ import annotations

import time

from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static


class ThinkingRow(Vertical, can_focus=True):
    """Collapsible muted block showing accumulated reasoning text."""

    DEFAULT_CSS = """
    ThinkingRow {
        margin: 0 3 1 3;
        padding: 0 1;
        height: auto;
        color: $text-muted;
        border-left: thick $accent-darken-2;
    }
    ThinkingRow:focus {
        border-left: thick $accent;
    }
    ThinkingRow > .thinking-header {
        height: 1;
        color: $text-muted;
    }
    ThinkingRow > .thinking-body {
        height: auto;
        color: $text-muted;
        padding: 0 0 0 1;
        display: none;
    }
    ThinkingRow.-expanded > .thinking-body {
        display: block;
    }
    """

    BINDINGS = [Binding("space", "toggle_expanded", "Expand/collapse", show=False)]

    expanded = reactive(False)
    sealed = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []
        self._started_at: float = time.monotonic()
        self._sealed_elapsed_s: float | None = None
        self._header = Static("", classes="thinking-header")
        self._body = Static("", classes="thinking-body")

    def compose(self):  # noqa: D401 — Textual idiom
        yield self._header
        yield self._body

    def on_mount(self) -> None:
        self._refresh_header()
        self._refresh_body()

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def append_delta(self, delta: str) -> None:
        """Append streaming reasoning text to the body."""
        if not delta:
            return
        self._buffer.append(delta)
        self._refresh_body()
        # Header carries the running elapsed-time counter; only refresh
        # while streaming so a sealed row's "thought (Xs)" stays stable.
        if not self.sealed:
            self._refresh_header()

    def seal(self) -> None:
        """Stop the elapsed-time counter; flip the header to past tense."""
        if self.sealed:
            return
        self._sealed_elapsed_s = time.monotonic() - self._started_at
        self.sealed = True
        self._refresh_header()

    def action_toggle_expanded(self) -> None:
        self.expanded = not self.expanded

    def watch_expanded(self, value: bool) -> None:  # noqa: FBT001
        self.set_class(value, "-expanded")
        self._refresh_header()

    # -- Internals ------------------------------------------------------

    def _refresh_header(self) -> None:
        glyph = "▾" if self.expanded else "▸"
        if self.sealed:
            elapsed = self._sealed_elapsed_s or 0.0
            label = f"thought ({elapsed:.1f}s)"
        else:
            elapsed = time.monotonic() - self._started_at
            label = f"thinking… ({elapsed:.1f}s)"
        self._header.update(f"{glyph} {label}")

    def _refresh_body(self) -> None:
        # Strip trailing newlines so a paint of the muted body doesn't
        # leave a stub bar after the visible content while streaming.
        self._body.update(self.text.rstrip("\n"))
