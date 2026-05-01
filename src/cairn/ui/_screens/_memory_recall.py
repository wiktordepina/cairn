"""Modal showing the top-k memories for a `/recall` query.

Read-only — there is nothing to dismiss with; Esc closes. Rows
display the composite score from `MemoryService.retrieve_scored`,
the entry type, importance, and the full content body. Score
visibility is intentional: parallels how `/cost` exposes raw
dollar figures rather than hiding them behind a debug flag.

Empty hit list renders a single line ("no memories matched that
query") rather than a stub row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult

    from cairn.domain import MemoryEntry


class MemoryRecallModal(ModalScreen[None]):
    """List the top-k memories for a query.

    Constructed with the original query (for the title) and a list of
    ``(score, MemoryEntry)`` pairs already sorted by descending score.
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Close"),
    ]

    DEFAULT_CSS = """
    MemoryRecallModal {
        align: center middle;
    }
    MemoryRecallModal > Vertical {
        width: 90%;
        max-width: 120;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $accent;
        background: $panel;
    }
    MemoryRecallModal .title {
        text-style: bold;
        padding-bottom: 1;
    }
    MemoryRecallModal .empty {
        color: $text-muted;
        padding: 1 0;
    }
    MemoryRecallModal VerticalScroll {
        height: auto;
        max-height: 30;
    }
    MemoryRecallModal .hit-header {
        color: $text-muted;
        padding-top: 1;
    }
    MemoryRecallModal .hit-body {
        padding-bottom: 1;
    }
    MemoryRecallModal .footer {
        color: $text-muted;
        padding-top: 1;
    }
    """

    def __init__(
        self,
        *,
        query: str,
        hits: Sequence[tuple[float, MemoryEntry]],
    ) -> None:
        super().__init__()
        self._query = query
        self._hits = tuple(hits)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f'recall: "{self._query}"  ({len(self._hits)} hit(s))', classes="title")
            if not self._hits:
                yield Label("no memories matched that query.", classes="empty")
                yield Label("esc: close", classes="footer")
                return
            with VerticalScroll():
                for score, entry in self._hits:
                    header = (
                        f"[{score:.2f}]  {entry.entry_type.value}  "
                        f"imp={entry.importance}  #{entry.id}"
                    )
                    yield Static(header, classes="hit-header")
                    yield Static(entry.content, classes="hit-body")
            yield Label("esc: close", classes="footer")

    def action_cancel(self) -> None:
        self.dismiss(None)
