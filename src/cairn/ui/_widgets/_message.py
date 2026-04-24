"""Message widget — plain text during streaming, Markdown on seal.

One `MessageView` per persisted or in-flight message. Assistant
messages stream in as plain text (many deltas per second); running
the Markdown parser on every delta leaves visible artefacts from
partially-parsed blocks, so streaming stays as-raw-as-received and
only swaps to the fully-parsed Markdown render once
`AssistantMessageComplete` fires. User messages are complete at
construction and render as Markdown immediately.
"""

from __future__ import annotations

from typing import Literal

from rich.markdown import Markdown as RichMarkdown
from textual.widgets import Static

Role = Literal["user", "assistant"]


class MessageView(Static):
    """A single message in the chat log.

    Holds an in-memory accumulator of the message text. For the
    assistant role, start with empty text and call `append_text` per
    delta — the rendered output is plain text until `seal()` swaps
    it for a rich Markdown render. For user messages, pass the full
    text at construction and the Markdown render happens there.
    """

    DEFAULT_CSS = """
    MessageView {
        margin: 0 2 1 2;
        padding: 0 1;
        height: auto;
    }
    MessageView.user {
        border-left: thick $primary;
    }
    MessageView.assistant {
        border-left: thick $success;
    }
    MessageView.assistant.-sealed {
        border-left: thick $success-darken-1;
    }
    """

    def __init__(
        self,
        *,
        message_id: str,
        role: Role,
        text: str = "",
    ) -> None:
        super().__init__("", id=_css_id(message_id))
        self._message_id = message_id
        self._role: Role = role
        self._buffer: list[str] = [text] if text else []
        self.add_class(role)
        if role == "user":
            # User messages arrive complete — render Markdown immediately.
            self._render_markdown()
        else:
            # Assistant messages stream in; plain text avoids
            # partial-parse artefacts.
            self._render_plain()

    @property
    def message_id(self) -> str:
        return self._message_id

    @property
    def role(self) -> Role:
        return self._role

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def append_text(self, delta: str) -> None:
        """Append a text delta. Re-renders as plain text — Markdown
        parsing is deferred to `seal()` to avoid streaming artefacts."""
        if not delta:
            return
        self._buffer.append(delta)
        self._render_plain()

    def seal(self) -> None:
        """Mark the message as complete — swap the plain-text
        render for a parsed Markdown render."""
        self.add_class("-sealed")
        self._render_markdown()

    # -- Internals ------------------------------------------------------

    def _render_plain(self) -> None:
        # Trailing newlines would render as an extra empty row the
        # `border-left` paints a stub bar on until `seal()` swaps in
        # the Markdown render. Strip only the trailing ones so
        # mid-buffer line breaks keep streaming correctly.
        self.update(self.text.rstrip("\n"))

    def _render_markdown(self) -> None:
        body = self.text
        self.update(RichMarkdown(body) if body else "")


def _css_id(message_id: str) -> str:
    """Map a message id (arbitrary str) to a valid Textual widget id.

    Textual requires ids to match ``[A-Za-z_][A-Za-z0-9_-]*``. Message
    ids in Cairn are UUID hex so almost any form works, but we
    defensively prefix + replace to keep this total.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in message_id)
    return f"msg-{safe}"
