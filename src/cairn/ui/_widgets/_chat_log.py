"""Scrolling container for chat-log children (messages, tool rows, banners)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll

from cairn.ui._widgets._message import MessageView
from cairn.ui._widgets._tool_row import ToolRow

if TYPE_CHECKING:
    from cairn.ui._widgets._banner import Banner


class ChatLog(VerticalScroll):
    """Scrolling chat transcript.

    Holds an ordered stack of `MessageView` / `ToolRow` / `Banner`
    widgets, newest last. Owns auto-scroll-on-append so the most
    recent widget is always visible while the user isn't scrolled up.
    """

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        padding-top: 1;
    }
    """

    def append_message(self, view: MessageView) -> None:
        self.mount(view)
        self.scroll_end(animate=False)

    def append_tool_row(self, row: ToolRow) -> None:
        self.mount(row)
        self.scroll_end(animate=False)

    def append_banner(self, banner: Banner) -> None:
        self.mount(banner)
        self.scroll_end(animate=False)

    _TAIL_SLACK = 3
    """Rows of slack before the bottom that still count as "at the tail".

    Streaming content can grow the max faster than the viewport
    snaps, so a narrow threshold leaves the user stranded mid-stream.
    Three rows is enough to absorb a paragraph-worth of deltas
    without fighting a user who scrolled up to read history."""

    def follow_tail(self) -> None:
        """Scroll to the bottom *iff* the user is already near it.

        Called by the screen whenever a child's content grows (most
        notably streaming assistant deltas), so live updates stay in
        view without yanking the viewport when the user has
        deliberately scrolled up to read earlier turns.
        """
        if self.scroll_y >= self.max_scroll_y - self._TAIL_SLACK:
            self.scroll_end(animate=False)

    def find_message(self, message_id: str) -> MessageView | None:
        for child in self.children:
            if isinstance(child, MessageView) and child.message_id == message_id:
                return child
        return None

    def find_tool_row(self, tool_call_id: str) -> ToolRow | None:
        for child in self.children:
            if isinstance(child, ToolRow) and child.tool_call_id == tool_call_id:
                return child
        return None
