"""The single-session chat screen.

Tranche 1 composes a `SessionHeader` + `ChatLog` + `CostMeter`. The
command bar lands in a follow-up commit on this branch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import Screen

from cairn.ui._widgets import ChatLog, CostMeter, MessageView, SessionHeader

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from cairn.domain import (
        AssistantMessageComplete,
        AssistantTextDelta,
        BudgetWarning,
        Session,
        TurnComplete,
        UserMessagePersisted,
    )


class SessionScreen(Screen[None]):
    """Screen holding one session's transcript.

    Widget surface is intentionally small for Tranche 1. The
    observer drives updates; the screen owns layout and the message
    lookup cache.
    """

    def __init__(self, *, session: Session) -> None:
        super().__init__()
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def compose(self) -> ComposeResult:
        yield SessionHeader(session=self._session)
        yield ChatLog(id="chat")
        yield CostMeter()

    # -- Observer callbacks ---------------------------------------------

    def append_user_message(self, event: UserMessagePersisted) -> None:
        """Mount a new `MessageView` for a just-persisted user message.

        The persisted row is the source of truth for content, but the
        orchestrator doesn't carry the text in the event — the
        currently-submitted user message is held on the screen while
        the turn runs (future work) or the screen re-reads from
        persistence. Tranche 1's test harness passes the text
        explicitly via `stage_user_message` below, so this method
        only sets up the `MessageView` slot for the id.
        """
        view = self._staged_user.pop(event.message_id, None)
        if view is None:
            view = MessageView(message_id=event.message_id, role="user")
        self._chat_log.append_message(view)

    def append_delta(self, event: AssistantTextDelta) -> None:
        """Append text to an assistant message, creating it on first delta."""
        view = self._chat_log.find_message(event.message_id)
        if view is None:
            view = MessageView(message_id=event.message_id, role="assistant")
            self._chat_log.append_message(view)
        view.append_text(event.text)

    def finalise_assistant_message(self, event: AssistantMessageComplete) -> None:
        """Mark an assistant message as sealed (no more deltas)."""
        view = self._chat_log.find_message(event.message_id)
        if view is not None:
            view.seal()

    def finalise_turn(self, event: TurnComplete) -> None:
        """Per-turn wrap-up hook. Filled in by the bootstrap wiring PR
        once the screen has a cost-tracker reference."""
        del event

    def set_cost(self, cost_usd: float, *, warn: bool = False) -> None:
        """Update the cost meter. Called by the bootstrap layer on
        `TurnComplete` (authoritative) and by the observer on
        `BudgetWarning` (warn=True)."""
        self._cost_meter.set_cost(cost_usd, warn=warn)

    def show_budget_warning(self, event: BudgetWarning) -> None:
        """Render the budget-warning state in the cost meter."""
        self.set_cost(event.cost_usd, warn=True)

    # -- Helpers for the pilot harness ----------------------------------

    def stage_user_message(self, *, message_id: str, text: str) -> None:
        """Pre-register the text for an upcoming `UserMessagePersisted`.

        The orchestrator's `UserMessagePersisted` event carries only
        the message id + turn id — not the text — because the text
        was supplied by the caller who persisted it. The app layer
        stages the text here so the observer can mount a widget with
        the right body when the event arrives.
        """
        self._staged_user[message_id] = MessageView(
            message_id=message_id,
            role="user",
            text=text,
        )

    # -- Private --------------------------------------------------------

    @property
    def _chat_log(self) -> ChatLog:
        return self.query_one("#chat", ChatLog)

    @property
    def _cost_meter(self) -> CostMeter:
        return self.query_one(CostMeter)

    _staged_user: dict[str, MessageView]

    def on_mount(self) -> None:
        self._staged_user = {}
