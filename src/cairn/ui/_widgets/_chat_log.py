"""Scrolling container for `MessageView` widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll

from cairn.ui._widgets._message import MessageView


class ChatLog(VerticalScroll):
    """Scrolling chat transcript.

    Holds an ordered stack of `MessageView` widgets, newest last.
    Owns auto-scroll-on-append semantics so the most recent message
    is always visible while the user isn't scrolled up.
    """

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
    }
    """

    def append_message(self, view: MessageView) -> None:
        """Mount a new message at the bottom of the log."""
        self.mount(view)
        self.scroll_end(animate=False)

    def find_message(self, message_id: str) -> MessageView | None:
        """Return the `MessageView` with *message_id* if it exists."""
        for child in self.children:
            if isinstance(child, MessageView) and child.message_id == message_id:
                return child
        return None
