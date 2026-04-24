"""Session header widget.

Shows the session-type badge (companion teal / persona amber /
ephemeral grey), persona name, model, and session title. Subscribes
to `CostMeter` siblings via Textual's reactive system only
indirectly — the meter owns its own state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal
from textual.widgets import Label

from cairn.ui._theme import colour_for

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from cairn.domain import Session
    from cairn.domain._enums import SessionType


class SessionTypeBadge(Label):
    """Coloured badge showing the session type.

    The accent colour is resolved from `_theme.colour_for` at
    construction. Config-driven overrides plug in via the
    `overrides` kwarg — the Tranche 1 default uses the built-in
    palette.
    """

    DEFAULT_CSS = """
    SessionTypeBadge {
        padding: 0 1;
        margin: 0 1 0 0;
        color: $text;
    }
    """

    def __init__(
        self,
        *,
        session_type: SessionType,
        overrides: dict[SessionType, str] | None = None,
    ) -> None:
        colour = colour_for(session_type, overrides=overrides)
        super().__init__(session_type.value)
        self._session_type = session_type
        self.styles.background = colour

    @property
    def session_type(self) -> SessionType:
        return self._session_type


class SessionHeader(Horizontal):
    """Top-of-screen header showing session identity + context.

    Layout: `[BADGE] persona · model · title`. The cost meter is a
    separate widget composed by `SessionScreen` so it can live in
    its own slot on the right.
    """

    DEFAULT_CSS = """
    SessionHeader {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    SessionHeader > Label {
        margin: 0 1 0 0;
    }
    """

    def __init__(
        self,
        *,
        session: Session,
        session_type_colour_overrides: dict[SessionType, str] | None = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._overrides = session_type_colour_overrides

    def compose(self) -> ComposeResult:
        yield SessionTypeBadge(
            session_type=self._session.type,
            overrides=self._overrides,
        )
        title = self._session.title or "(untitled)"
        yield Label(f"{self._session.persona} · {self._session.model} · {title}")
