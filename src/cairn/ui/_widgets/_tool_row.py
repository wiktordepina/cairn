"""Inline tool-call status row.

One `ToolRow` widget per tool call, shown in the chat log between
assistant messages. Progresses through: planning → approved →
running → completed | rejected | failed | timed_out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Label

if TYPE_CHECKING:
    from cairn.domain._enums import ToolCallStatus


class ToolRow(Label):
    """Single-line status indicator for a tool call."""

    DEFAULT_CSS = """
    ToolRow {
        margin: 0 3;
        padding: 0 1;
        color: $text-muted;
    }
    ToolRow.-running {
        color: $accent;
    }
    ToolRow.-completed {
        color: $success;
    }
    ToolRow.-error {
        color: $error;
    }
    """

    def __init__(self, *, tool_call_id: str, tool_name: str) -> None:
        super().__init__(f"⚙ planning: {tool_name}…")
        self._tool_call_id = tool_call_id
        self._tool_name = tool_name
        self._status: ToolCallStatus | None = None

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def status(self) -> ToolCallStatus | None:
        return self._status

    def mark_approved(self, approved_by: str) -> None:
        self.update(f"⚙ approved ({approved_by}): {self._tool_name}…")

    def mark_rejected(self, decided_by: str, reason: str | None) -> None:
        tail = f" — {reason}" if reason else ""
        self.update(f"⚙ rejected ({decided_by}): {self._tool_name}{tail}")
        self.set_class(True, "-error")

    def mark_running(self) -> None:
        self.update(f"⚙ running: {self._tool_name}…")
        self.set_class(True, "-running")

    def mark_completed(
        self,
        *,
        status: ToolCallStatus,
        is_error: bool,
        duration_ms: int | None,
    ) -> None:
        self._status = status
        duration = f" · {duration_ms} ms" if duration_ms is not None else ""
        glyph = "✗" if is_error else "✓"
        self.update(f"{glyph} {status.value}: {self._tool_name}{duration}")
        self.set_class(False, "-running")
        self.set_class(is_error, "-error")
        self.set_class(not is_error, "-completed")
