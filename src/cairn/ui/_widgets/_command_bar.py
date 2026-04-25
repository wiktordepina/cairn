"""Command bar — user input widget.

A `TextArea`-backed prompt that auto-grows from one row up to a row
cap as the user types or pastes. Submitting the line emits a custom
`CommandBar.Submitted` message that mirrors the old `Input.Submitted`
shape (``input``, ``value``) so the session screen handler stays
unchanged in spirit.

Keyboard:
- ``enter`` submits the line (or, when the completion menu is open,
  picks the highlighted command).
- ``shift+enter`` / ``alt+enter`` / ``ctrl+j`` insert a newline. The
  triple-binding is deliberate: shift+enter relies on terminal
  keyboard-protocol support (kitty / modern xterm), and we want a
  reliable fallback in legacy terminals.
- ``up`` / ``down`` drive the completion menu when it's open, and walk
  the prompt-history ring otherwise. History walking is suppressed
  mid-text (when the cursor isn't at the top/bottom line) so the keys
  still behave like a normal text editor while you're inside a
  multi-line draft.
- ``tab`` fills the highlighted completion. ``escape`` closes it.

The optional `CompletionMenu` sibling is attached via
`bind_completion`; when set, navigation keys drive the menu first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.message import Message
from textual.widgets import TextArea

if TYPE_CHECKING:
    from textual import events

    from cairn.ui._widgets._completion import CompletionMenu

_DEFAULT_MAX_ROWS = 10


class CommandBar(TextArea):
    """Multi-line, auto-growing prompt + command bar."""

    DEFAULT_CSS = """
    CommandBar {
        border: round $accent;
        margin: 0 1 1 1;
        height: auto;
        min-height: 3;
        max-height: 12;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar = [
        ("shift+enter", "newline", "Newline"),
        ("alt+enter", "newline", "Newline"),
        ("ctrl+j", "newline", "Newline"),
    ]

    class Submitted(Message):
        """Emitted when the user presses Enter on a non-empty bar.

        Mirrors `Input.Submitted` shape so the session screen handler
        signature (``event.input``, ``event.value``) stays stable.
        """

        def __init__(self, command_bar: CommandBar, value: str) -> None:
            super().__init__()
            self.input: CommandBar = command_bar
            self.value: str = value

        @property
        def control(self) -> CommandBar:
            return self.input

    def __init__(
        self,
        *,
        completion: CompletionMenu | None = None,
        max_rows: int = _DEFAULT_MAX_ROWS,
        history: list[str] | None = None,
    ) -> None:
        super().__init__(id="cmd")
        self._completion = completion
        self._max_rows = max_rows
        # History is a chronologically-ordered ring of previously-submitted
        # prompts (oldest → newest). The cursor is None when the user is
        # editing a fresh draft; when they press Up it snaps to the most
        # recent entry and walks back from there.
        self._history: list[str] = list(history) if history else []
        self._history_cursor: int | None = None
        self._draft_before_history: str = ""

    # -- Public API -----------------------------------------------------

    @property
    def value(self) -> str:
        """Compatibility alias for `Input.value`."""
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        self.text = new_value

    def bind_completion(self, completion: CompletionMenu) -> None:
        """Attach a `CompletionMenu` sibling for this bar to drive."""
        self._completion = completion

    def set_prompt_history(self, history: list[str]) -> None:
        """Replace the in-memory prompt-history ring (oldest → newest).

        Distinct from `TextArea.history` (its undo/redo edit log).
        """
        self._history = list(history)
        self._history_cursor = None

    def push_history(self, entry: str) -> None:
        """Append a prompt to the in-memory history.

        Persistence (writing to disk) is the responsibility of the
        screen that owns the bar — keeping this widget I/O-free keeps
        unit tests fast.
        """
        if not entry.strip():
            return
        # Drop adjacent duplicates so repeated up-arrow doesn't replay
        # the same prompt twice.
        if self._history and self._history[-1] == entry:
            return
        self._history.append(entry)
        self._history_cursor = None

    @property
    def prompt_history(self) -> list[str]:
        """Snapshot of the prompt-history ring (oldest → newest).

        Renamed away from ``history`` to avoid colliding with
        ``TextArea.history`` (the undo/redo `EditHistory`).
        """
        return list(self._history)

    # -- Actions --------------------------------------------------------

    def action_newline(self) -> None:
        self.insert("\n")

    async def action_submit(self) -> None:
        """Programmatic submit hook used by the Pilot test harness.

        Mirrors the old `Input.action_submit` entry point so tests can
        stage a value and trigger the same code path the Enter
        keystroke would. Awaitable to match the original signature.
        """
        self._submit_current()

    # -- Key handling ---------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        # Active history walk wins over the completion menu: walking
        # to a slash-command in history would otherwise auto-open the
        # menu and steal Up/Down, leaving the user stuck on the
        # newest entry. Tab / Enter / Escape still go to the menu
        # because those are intentional menu interactions; arrow keys
        # while walking continue the walk.
        if event.key in ("up", "down") and self._history_cursor is not None:
            direction = -1 if event.key == "up" else 1
            self._step_history(direction)
            event.prevent_default()
            event.stop()
            return

        # Completion menu wins next: while it's open, up / down / tab /
        # enter / escape drive the menu rather than the editor or
        # history ring.
        menu = self._completion
        if menu is not None and menu.is_open:
            if event.key == "down":
                menu.move_down()
            elif event.key == "up":
                menu.move_up()
            elif event.key == "tab":
                name = menu.selected_name
                if name is not None:
                    self.value = name + " "
                    self.cursor_location = self._end_location()
            elif event.key == "enter":
                name = menu.selected_name
                if name is None:
                    self._submit_current()
                    event.prevent_default()
                    event.stop()
                    return
                self.value = name
                self.cursor_location = self._end_location()
                self._submit_current()
            elif event.key == "escape":
                menu.close()
            else:
                return
            event.prevent_default()
            event.stop()
            return

        if event.key == "enter":
            self._submit_current()
            event.prevent_default()
            event.stop()
            return

        if event.key == "up":
            row, _ = self.cursor_location
            if row == 0 and self._history:
                self._step_history(-1)
                event.prevent_default()
                event.stop()
            return

        if event.key == "down":
            row, _ = self.cursor_location
            if self._history_cursor is not None and row == self.document.line_count - 1:
                self._step_history(1)
                event.prevent_default()
                event.stop()
            return

    # -- TextArea hooks -------------------------------------------------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Reset the history cursor on freehand edits.

        TextArea.Changed is posted asynchronously and one
        ``_set_text_silently`` call can race ahead of multiple queued
        messages, so a counter / boolean flag is brittle (rapid
        Up/Down would deplete it and let a stale message reset the
        cursor mid-walk). We instead compare the current text against
        the entry the cursor currently points to: matching means the
        change came from a walk and the cursor stays; anything else
        is a real user edit that should snap us back to draft mode.
        """
        if event.text_area is not self:
            return
        cursor = self._history_cursor
        if (
            cursor is not None
            and 0 <= cursor < len(self._history)
            and self.text == self._history[cursor]
        ):
            return
        self._history_cursor = None

    # -- Internals ------------------------------------------------------

    def _submit_current(self) -> None:
        value = self.text
        # `clear()` resets cursor + history state; we want the value as
        # it was at submit time.
        self.text = ""
        self._history_cursor = None
        self._draft_before_history = ""
        self.post_message(CommandBar.Submitted(self, value))

    def _step_history(self, direction: int) -> None:
        """Walk the history ring; `direction` is -1 (older) or +1 (newer)."""
        if not self._history:
            return
        if self._history_cursor is None:
            if direction != -1:
                return
            self._draft_before_history = self.text
            self._history_cursor = len(self._history) - 1
        else:
            new_idx = self._history_cursor + direction
            if new_idx < 0:
                self._history_cursor = 0
            elif new_idx >= len(self._history):
                self._history_cursor = None
                self._set_text_silently(self._draft_before_history)
                self._draft_before_history = ""
                return
            else:
                self._history_cursor = new_idx
        self._set_text_silently(self._history[self._history_cursor])

    def _set_text_silently(self, value: str) -> None:
        """Replace the text with a history entry.

        ``on_text_area_changed`` distinguishes walk vs edit by content
        (does the new text match ``_history[_history_cursor]``?), so
        no suppression flag is needed here.
        """
        self.text = value
        self.cursor_location = self._end_location()

    def _end_location(self) -> tuple[int, int]:
        last_row = max(0, self.document.line_count - 1)
        last_line = self.document.get_line(last_row)
        return (last_row, len(last_line))
