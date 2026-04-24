"""The single-session chat screen.

Tranche 1 ships a minimal composition: a `ChatLog` for the
transcript. The command bar, session header, and cost meter land in
follow-up commits on this branch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import Screen

from cairn.ui._widgets import ChatLog, MessageView

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from cairn.domain import (
        AssistantMessageComplete,
        AssistantTextDelta,
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
        yield ChatLog(id="chat")

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
        """Hook for per-turn wrap-up. Tranche 1 is a no-op; the
        cost meter lands in a follow-up commit."""
        del event  # unused in Tranche 1

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

    _staged_user: dict[str, MessageView]

    def on_mount(self) -> None:
        self._staged_user = {}
