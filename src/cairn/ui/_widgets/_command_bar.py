"""Command bar — user input widget.

A single-line `Input` that accepts either plain chat text (submitted
as a user message) or a slash command. The Input widget's submission
message is caught by the session screen, which routes to the
command registry or the orchestrator depending on the prefix.
"""

from __future__ import annotations

from textual.widgets import Input


class CommandBar(Input):
    """One-line input + command bar."""

    DEFAULT_CSS = """
    CommandBar {
        border: round $accent;
        margin: 0 1 1 1;
    }
    """

    def __init__(self) -> None:
        super().__init__(placeholder="Type a message or /command…", id="cmd")
