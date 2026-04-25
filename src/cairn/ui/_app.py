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
    from collections.abc import Awaitable, Callable

    from cairn.config import ModelRegistry, ProfileConfig, UIConfig
    from cairn.conventions import AllowlistStore, ConventionLoader
    from cairn.domain import Session
    from cairn.orchestrator import Orchestrator
    from cairn.orchestrator._protocols import ToolRegistry
    from cairn.ui._context_report import ContextReportInput

    CostSource = Callable[[], Awaitable[float]]
    ContextSource = Callable[[], Awaitable[ContextReportInput]]


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
        ui_config: UIConfig | None = None,
        cost_source: CostSource | None = None,
        context_source: ContextSource | None = None,
        resumed_turn_count: int = 0,
        profile: ProfileConfig | None = None,
        model_registry: ModelRegistry | None = None,
        convention_loader: ConventionLoader | None = None,
        allowlist_store: AllowlistStore | None = None,
    ) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._session = session
        self._session_screen: SessionScreen | None = None
        self._command_registry = command_registry or build_default_registry()
        self._tool_registry = tool_registry
        self._cost_source = cost_source
        self._context_source = context_source
        self._pending_resumed_turn_count = resumed_turn_count
        self._profile = profile
        self._model_registry = model_registry
        self._convention_loader = convention_loader
        self._allowlist_store = allowlist_store
        from cairn.config import UIConfig as _UIConfig

        self._ui_config = ui_config or _UIConfig()

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

    @property
    def ui_config(self) -> UIConfig:
        """Active profile's UI block (defaults when unset)."""
        return self._ui_config

    @property
    def profile(self) -> ProfileConfig | None:
        """Active `ProfileConfig`, when wired by the bootstrap.

        `None` when the app is minted with mock collaborators (the
        Pilot test harness pattern).
        """
        return self._profile

    @property
    def model_registry(self) -> ModelRegistry | None:
        """Shared `ModelRegistry` for read-only UI lookups (e.g. `/model`)."""
        return self._model_registry

    @property
    def convention_loader(self) -> ConventionLoader | None:
        """Shared `ConventionLoader` for `/conventions`."""
        return self._convention_loader

    @property
    def allowlist_store(self) -> AllowlistStore | None:
        """User-level project allowlist for `/conventions` trust state."""
        return self._allowlist_store

    def take_resumed_turn_count(self) -> int:
        """Return the recorded count and clear it.

        Screens read this on mount to surface a one-time crash-recovery
        banner; subsequent screens see zero, which is what we want —
        the count refers to the boot-time sweep, not ongoing state.
        """
        count = self._pending_resumed_turn_count
        self._pending_resumed_turn_count = 0
        return count

    async def on_mount(self) -> None:
        screen = SessionScreen(
            session=self._session,
            cost_precision=self._ui_config.cost_display_precision,
            cost_source=self._cost_source,
            context_source=self._context_source,
        )
        self._session_screen = screen
        await self.push_screen(screen)
