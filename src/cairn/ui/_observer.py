"""Sync observer that routes `UIEvent`s to `SessionScreen` mutations.

Implements the orchestrator's `UIEventObserver` protocol. The
observer receives events from three producers:

1. The per-turn event stream from `Orchestrator.run_turn`.
2. The orchestrator's synchronous fan-out path (session-lifecycle
   events).
3. The extraction queue's fan-out
   (`ObservationExtractionRequested` / `...Completed`).

All three producers run on the same asyncio event loop as the
Textual app (Cairn is single-process, single-loop), so widget
mutations happen via direct method calls. Exceptions are logged
and swallowed — this observer must never break a turn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cairn.domain import (
    AssistantMessageComplete,
    AssistantTextDelta,
    BudgetOverflowAdvisory,
    BudgetWarning,
    ConfigDriftDetected,
    HistoryCompacted,
    ToolCallApproved,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallRejected,
    ToolCallStarted,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
    TurnIncomplete,
    UserMessagePersisted,
)

if TYPE_CHECKING:
    from cairn.domain import UIEvent
    from cairn.ui._app import CairnApp


log = logging.getLogger(__name__)


class TextualUIEventObserver:
    """`UIEventObserver` that posts widget updates to `CairnApp`.

    The observer itself holds no mutable state. It resolves the
    current session screen lazily from the app so it works across
    session switches (Tranche 2+); in Tranche 1 there's only ever
    one screen.
    """

    def __init__(self, app: CairnApp) -> None:
        self._app = app

    def observe(self, event: UIEvent) -> None:
        """Dispatch *event* to the appropriate screen method.

        Dispatch is synchronous from the orchestrator's perspective;
        the screen method is marshalled onto the UI thread via
        `call_from_thread`. Individual branches catch and log their
        own errors so one malformed event doesn't break the observer.
        """
        try:
            self._route(event)
        except Exception:  # noqa: BLE001
            log.exception("TextualUIEventObserver: failed to route %r", type(event).__name__)

    def _route(self, event: UIEvent) -> None:
        screen = self._app.current_session_screen
        if screen is None:
            # No session mounted yet — session-lifecycle events can
            # precede the screen in the mount sequence. Drop silently;
            # the screen reads persisted state on mount.
            return

        match event:
            case UserMessagePersisted():
                screen.append_user_message(event)
            case AssistantTextDelta():
                screen.append_delta(event)
            case AssistantMessageComplete():
                screen.finalise_assistant_message(event)
            case ToolCallPlanned():
                screen.note_tool_plan(event)
            case ToolCallApproved():
                screen.mark_tool_approved(event)
            case ToolCallRejected():
                screen.mark_tool_rejected(event)
            case ToolCallStarted():
                screen.mark_tool_started(event)
            case ToolCallCompleted():
                screen.mark_tool_completed(event)
            case TurnComplete():
                screen.finalise_turn(event)
            case TurnAborted():
                screen.show_aborted(event)
            case TurnBlocked():
                screen.show_blocked(event)
            case TurnIncomplete():
                screen.show_incomplete(event)
            case BudgetWarning():
                screen.show_budget_warning(event)
            case HistoryCompacted():
                screen.show_compaction(event)
            case BudgetOverflowAdvisory():
                screen.show_overflow_advisory(event)
            case ConfigDriftDetected():
                screen.show_drift(event)
            case _:
                # Session-lifecycle, delegation, and observation-
                # extraction events are Tranche 2 work. Silent no-op
                # keeps forward compat for the event stream.
                pass
