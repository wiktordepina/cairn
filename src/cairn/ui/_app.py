"""The top-level Textual app.

`CairnApp` owns the session screen, the orchestrator reference, and
the observer that routes `UIEvent`s back to widgets. Tranche 1's
composition is a single session; session-list sidebar + multiple
screens land in Tranche 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App

from cairn.ui._commands import CommandRegistry, build_default_registry
from cairn.ui._screens import SessionScreen

if TYPE_CHECKING:
    from cairn.domain import Session
    from cairn.orchestrator import Orchestrator
    from cairn.orchestrator._protocols import ToolRegistry


class CairnApp(App[None]):
    """Cairn's Textual application.

    The app is constructed with an already-wired orchestrator and an
    initial session. Bootstrap (logger, SSL, orchestrator assembly)
    lives outside the app — see `cairn.ui._bootstrap` in a follow-up
    commit.
    """

    TITLE = "Cairn"

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        session: Session,
        command_registry: CommandRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._session = session
        self._session_screen: SessionScreen | None = None
        self._command_registry = command_registry or build_default_registry()
        self._tool_registry = tool_registry

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    @property
    def current_session_screen(self) -> SessionScreen | None:
        """Return the currently-mounted session screen, or None."""
        return self._session_screen

    @property
    def command_registry(self) -> CommandRegistry:
        return self._command_registry

    @property
    def tool_registry(self) -> ToolRegistry | None:
        """Shared `ToolRegistry` for read-only UI lookups (e.g. `/tools`).

        Set by the bootstrap layer when it constructs the orchestrator;
        `None` when the app is minted with a mock orchestrator.
        """
        return self._tool_registry

    async def on_mount(self) -> None:
        screen = SessionScreen(session=self._session)
        self._session_screen = screen
        await self.push_screen(screen)
