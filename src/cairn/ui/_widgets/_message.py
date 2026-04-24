"""Streaming-capable Markdown message widget.

One `MessageView` per persisted or in-flight message. For the
assistant's streaming output the widget accumulates text deltas
and rewrites the rendered Markdown on each tick. For user messages
the text is set once at construction.
"""

from __future__ import annotations

from typing import Literal

from textual.widgets import Markdown

Role = Literal["user", "assistant"]


class MessageView(Markdown):
    """A single message rendered as Markdown.

    Holds an in-memory accumulator of the message text so streaming
    updates can re-render the full content cheaply. For the assistant
    role, start with empty text and call `append_text` per delta; for
    user messages, pass the full text at construction.
    """

    DEFAULT_CSS = """
    MessageView {
        margin: 1 2;
        padding: 0 1;
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
        super().__init__(markdown=text, id=_css_id(message_id))
        self._message_id = message_id
        self._role: Role = role
        self._buffer: list[str] = [text] if text else []
        self.add_class(role)

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
        """Append a text delta and re-render the Markdown body."""
        if not delta:
            return
        self._buffer.append(delta)
        self.update("".join(self._buffer))

    def seal(self) -> None:
        """Mark the message as complete — the streaming affordance
        dims to indicate no more deltas are coming."""
        self.add_class("-sealed")


def _css_id(message_id: str) -> str:
    """Map a message id (arbitrary str) to a valid Textual widget id.

    Textual requires ids to match ``[A-Za-z_][A-Za-z0-9_-]*``. Message
    ids in Cairn are UUID hex so almost any form works, but we
    defensively prefix + replace to keep this total.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in message_id)
    return f"msg-{safe}"
