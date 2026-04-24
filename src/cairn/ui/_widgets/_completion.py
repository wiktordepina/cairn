"""Slash-command completion menu.

A compact `OptionList` shown above the command bar while the user is
typing a slash command. The menu itself is focus-less — the command
bar keeps focus and forwards navigation keys (up / down / tab / esc)
via its own `on_key`, so text entry remains uninterrupted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from cairn.ui._commands import CommandRegistry, SlashCommand


class CompletionMenu(OptionList):
    """Popover menu of matching slash commands.

    Visibility is driven by `sync()`: empty input or text that has
    passed the command name (whitespace introduces arguments) closes
    the menu. Selection is recorded via `OptionList.highlighted`;
    there is no "select" event because the command bar drives
    completion directly by reading `selected_name`.
    """

    DEFAULT_CSS = """
    CompletionMenu {
        display: none;
        height: auto;
        max-height: 8;
        margin: 0 1;
        border: round $accent;
        background: $surface;
    }
    CompletionMenu.-visible {
        display: block;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 — Textual widget kw
        super().__init__(id=id)
        self._matches: list[SlashCommand] = []

    # -- Public API -----------------------------------------------------

    @property
    def is_open(self) -> bool:
        return bool(self._matches)

    @property
    def matches(self) -> list[SlashCommand]:
        return list(self._matches)

    @property
    def selected_name(self) -> str | None:
        """Name of the currently-highlighted command, or None if the
        menu is closed or nothing is selected."""
        if not self._matches:
            return None
        idx = self.highlighted
        if idx is None:
            return None
        return self._matches[idx].name

    def sync(self, value: str, registry: CommandRegistry) -> None:
        """Recompute visibility + options from the command-bar value.

        Opens the menu when `value` is a pure slash-command prefix
        (starts with `/`, no whitespace) and at least one command
        matches. Closes otherwise.
        """
        if not value.startswith("/") or any(ch.isspace() for ch in value):
            self.close()
            return
        matches = registry.match(value)
        if not matches:
            self.close()
            return
        self._matches = matches
        self.clear_options()
        self.add_options(Option(f"{cmd.name} — {cmd.summary}") for cmd in matches)
        self.highlighted = 0
        self.add_class("-visible")

    def close(self) -> None:
        self._matches = []
        self.clear_options()
        self.remove_class("-visible")

    def move_up(self) -> None:
        if self._matches:
            self.action_cursor_up()

    def move_down(self) -> None:
        if self._matches:
            self.action_cursor_down()
