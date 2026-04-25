"""Command bar — user input widget.

A single-line `Input` that accepts either plain chat text (submitted
as a user message) or a slash command. The Input widget's submission
message is caught by the session screen, which routes to the
command registry or the orchestrator depending on the prefix.

Optionally owns a reference to a `CompletionMenu` sibling; when set,
the bar intercepts navigation keys (up / down / tab / enter / esc)
and drives the menu instead of falling through to default behaviour.
Enter on a highlighted match swaps the input value for the selected
command name and lets the default submit path dispatch it — a single
keystroke runs the command, no Tab-then-Enter dance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Input

if TYPE_CHECKING:
    from textual import events

    from cairn.ui._widgets._completion import CompletionMenu


class CommandBar(Input):
    """One-line input + command bar."""

    DEFAULT_CSS = """
    CommandBar {
        border: round $accent;
        margin: 0 1 1 1;
    }
    """

    def __init__(self, *, completion: CompletionMenu | None = None) -> None:
        super().__init__(placeholder="Type a message or /command…", id="cmd")
        self._completion = completion

    def bind_completion(self, completion: CompletionMenu) -> None:
        """Attach a `CompletionMenu` for this bar to drive.

        Exposed separately from the constructor so the session screen
        can compose the menu and bar in either order — the screen
        calls this after both are instantiated.
        """
        self._completion = completion

    async def on_key(self, event: events.Key) -> None:
        menu = self._completion
        if menu is None or not menu.is_open:
            return
        if event.key == "down":
            menu.move_down()
        elif event.key == "up":
            menu.move_up()
        elif event.key == "tab":
            name = menu.selected_name
            if name is not None:
                # Trailing space closes the menu (whitespace introduces
                # arguments) and puts the caret past the command name
                # so the user can start typing args immediately.
                self.value = name + " "
                self.cursor_position = len(self.value)
        elif event.key == "enter":
            name = menu.selected_name
            if name is None:
                return  # fall through to Input's default submit
            # Replace the typed prefix with the highlighted command and
            # let Input's default Enter handler fire `Input.Submitted`,
            # which the session screen routes through the registry.
            self.value = name
            self.cursor_position = len(self.value)
            return
        elif event.key == "escape":
            menu.close()
        else:
            return
        event.prevent_default()
        event.stop()
