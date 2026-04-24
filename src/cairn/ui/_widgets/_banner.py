"""Inline banner for turn-level signals.

Covers aborted / blocked / incomplete / compaction / overflow
advisory events — single-line coloured messages mounted inline in
the chat log.
"""

from __future__ import annotations

from typing import Literal

from textual.widgets import Label

BannerKind = Literal["error", "warning", "info", "muted"]


class Banner(Label):
    """One-line inline banner.

    Kind drives the colour class. Banners are mounted in the chat
    log alongside message views — the assumption is that ordering
    matters for the user's mental model.
    """

    DEFAULT_CSS = """
    Banner {
        margin: 0 2 1 2;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    Banner.-error {
        color: $error;
        border-left: thick $error;
    }
    Banner.-warning {
        color: $warning;
        border-left: thick $warning;
    }
    Banner.-info {
        color: $text;
        border-left: thick $accent;
    }
    Banner.-muted {
        color: $text-muted;
    }
    """

    def __init__(self, *, text: str, kind: BannerKind = "info") -> None:
        super().__init__(text)
        self._kind: BannerKind = kind
        self.add_class(f"-{kind}")

    @property
    def kind(self) -> BannerKind:
        return self._kind
