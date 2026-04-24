"""The single-session chat screen.

Tranche 1 composition: `SessionHeader` + `ChatLog` + `CommandBar` +
`CostMeter`. `CommandBar.Submitted` routes to the slash-command
registry (when the input starts with `/`) or to the orchestrator as
a user-message turn (to be wired in the bootstrap PR).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.screen import Screen

from cairn.ui._widgets import (
    Banner,
    ChatLog,
    CommandBar,
    CostMeter,
    MessageView,
    SessionHeader,
    ToolRow,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from cairn.domain import (
        AssistantMessageComplete,
        AssistantTextDelta,
        BudgetOverflowAdvisory,
        BudgetWarning,
        HistoryCompacted,
        Session,
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
    from cairn.ui._app import CairnApp


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
        yield CommandBar()
        yield CostMeter()

    # -- Command bar wiring ---------------------------------------------

    async def on_input_submitted(self, event: CommandBar.Submitted) -> None:
        """Handle Enter in the command bar.

        `/command` forms dispatch through the registry; plain text is
        mounted in the chat log (turn-dispatch wiring lands with the
        bootstrap PR).
        """
        if not isinstance(event.input, CommandBar):
            return
        line = event.value
        event.input.clear()
        if line.startswith("/"):
            app = cast("CairnApp", self.app)
            result = await app.command_registry.dispatch(app, line)
            if result.status == "unknown":
                self._chat_log.append_banner(
                    Banner(text=result.message or "unknown command", kind="error")
                )
            return
        # Plain text: mount immediately so the user sees their input.
        # The orchestrator handoff for turn execution lands with the
        # bootstrap PR; for now the typed text is visible in the log.
        text = line.strip()
        if not text:
            return
        from uuid import uuid4

        message_id = f"pending-{uuid4().hex[:8]}"
        self._chat_log.append_message(MessageView(message_id=message_id, role="user", text=text))

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

    # -- Tool-call observer callbacks -----------------------------------

    def note_tool_plan(self, event: ToolCallPlanned) -> None:
        """Mount a new `ToolRow` for a freshly-planned tool call."""
        row = ToolRow(tool_call_id=event.tool_call_id, tool_name=event.tool_name)
        self._chat_log.append_tool_row(row)

    def mark_tool_approved(self, event: ToolCallApproved) -> None:
        row = self._chat_log.find_tool_row(event.tool_call_id)
        if row is not None:
            row.mark_approved(event.approved_by)

    def mark_tool_rejected(self, event: ToolCallRejected) -> None:
        row = self._chat_log.find_tool_row(event.tool_call_id)
        if row is not None:
            row.mark_rejected(event.decided_by, event.reason)

    def mark_tool_started(self, event: ToolCallStarted) -> None:
        row = self._chat_log.find_tool_row(event.tool_call_id)
        if row is not None:
            row.mark_running()

    def mark_tool_completed(self, event: ToolCallCompleted) -> None:
        row = self._chat_log.find_tool_row(event.tool_call_id)
        if row is not None:
            row.mark_completed(
                status=event.status,
                is_error=event.is_error,
                duration_ms=event.duration_ms,
            )

    # -- Turn-state observer callbacks ----------------------------------

    def show_aborted(self, event: TurnAborted) -> None:
        detail = event.message or event.reason
        self._chat_log.append_banner(Banner(text=f"✗ turn aborted — {detail}", kind="error"))

    def show_blocked(self, event: TurnBlocked) -> None:
        self._chat_log.append_banner(
            Banner(text=f"◷ turn blocked — {event.message}", kind="warning")
        )

    def show_incomplete(self, event: TurnIncomplete) -> None:
        del event
        self._chat_log.append_banner(
            Banner(text="… stopped at max_tokens with a partial tool call", kind="muted")
        )

    def show_compaction(self, event: HistoryCompacted) -> None:
        text = (
            f"⇣ history compacted ({event.reason}): "
            f"dropped {event.messages_dropped} messages, "
            f"{event.tokens_before} → {event.tokens_after} tokens"
        )
        self._chat_log.append_banner(Banner(text=text, kind="muted"))

    def show_overflow_advisory(self, event: BudgetOverflowAdvisory) -> None:
        # Full modal dialog lands in Tranche 3; Tranche 1 surfaces a
        # prominent warning banner so advisory-overflow isn't silent.
        text = (
            f"⚠ context-budget overflow: projected {event.tokens_projected} tokens "
            f"vs {event.context_window}-token window "
            f"(+{event.overflow_tokens})"
        )
        self._chat_log.append_banner(Banner(text=text, kind="warning"))

    # -- Public helpers (for command handlers + bootstrap) --------------

    def append_banner(self, banner: Banner) -> None:
        """Mount an ad-hoc banner in the chat log."""
        self._chat_log.append_banner(banner)

    @property
    def current_cost_usd(self) -> float:
        """Current cost reading on the cost meter."""
        return self._cost_meter.cost_usd

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
