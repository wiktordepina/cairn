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
    ActivityIndicator,
    Banner,
    ChatLog,
    CommandBar,
    CompletionMenu,
    CostMeter,
    MessageView,
    SessionHeader,
    ThinkingRow,
    ToolRow,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from textual.app import ComposeResult

    from cairn.domain import (
        AssistantMessageComplete,
        AssistantTextDelta,
        AssistantThinkingDelta,
        BudgetOverflowAdvisory,
        BudgetWarning,
        ConfigDriftDetected,
        DelegationCompleted,
        DelegationSpawned,
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
    from cairn.ui._context_report import ContextReportInput


class SessionScreen(Screen[None]):
    """Screen holding one session's transcript.

    Widget surface is intentionally small for Tranche 1. The
    observer drives updates; the screen owns layout and the message
    lookup cache.
    """

    def __init__(
        self,
        *,
        session: Session,
        cost_precision: int = 6,
        cost_source: Callable[[], Awaitable[float]] | None = None,
        context_source: Callable[[], Awaitable[ContextReportInput]] | None = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._cost_precision = cost_precision
        self._cost_source = cost_source
        self._context_source = context_source

    @property
    def context_source(
        self,
    ) -> Callable[[], Awaitable[ContextReportInput]] | None:
        """Optional async callback surfacing `(context_window, model, last_usage)`
        for the `/context` slash command. None in tests with mock
        orchestrators; bootstrap-supplied in production."""
        return self._context_source

    @property
    def session(self) -> Session:
        return self._session

    def compose(self) -> ComposeResult:
        yield SessionHeader(session=self._session)
        yield ChatLog(id="chat")
        completion = CompletionMenu(id="completion")
        yield completion
        # Sits just above the command bar so the user can see what
        # the agent is doing without taking their eyes off where they
        # type. Docking it in the header was too far from the input.
        yield ActivityIndicator()
        yield CommandBar(completion=completion)
        yield CostMeter(precision=self._cost_precision)

    # -- Command bar wiring ---------------------------------------------

    def on_text_area_changed(self, event: object) -> None:
        """Keep the completion menu in sync with the bar's value.

        Filters TextArea.Changed events to only those originating from
        the CommandBar — other TextAreas in nested screens shouldn't
        drive the completion menu.
        """
        text_area = getattr(event, "text_area", None)
        if not isinstance(text_area, CommandBar):
            return
        app = cast("CairnApp", self.app)
        self._completion.sync(text_area.text, app.command_registry)

    async def on_command_bar_submitted(self, event: CommandBar.Submitted) -> None:
        """Handle Enter in the command bar.

        `/command` forms dispatch through the registry; plain text is
        submitted to the orchestrator as a user turn — the observer
        then drives the transcript, cost meter, and tool rows as
        events arrive. The bar has already cleared itself by this
        point; this handler is purely about routing the value.
        """
        line = event.value
        # Dismiss the completion menu on any submit — the value has
        # been cleared and the popover would otherwise linger after
        # the command runs.
        self._completion.close()
        if line.startswith("/"):
            event.input.push_history(line)
            self._persist_history(event.input)
            app = cast("CairnApp", self.app)
            result = await app.command_registry.dispatch(app, line)
            if result.status == "unknown":
                self._chat_log.append_banner(
                    Banner(text=result.message or "unknown command", kind="error")
                )
            return
        text = line.strip()
        if not text:
            return
        event.input.push_history(line)
        self._persist_history(event.input)
        self._dispatch_turn(text)

    def _dispatch_turn(self, text: str) -> None:
        """Build a user `Message`, stage its text, kick off a worker
        that drives the orchestrator turn. Event-driven UI updates
        arrive via the observer on the same event loop."""
        from cairn.domain._content import TextBlock
        from cairn.domain._messages import Message

        user_msg = Message(role="user")
        user_msg.content.append(TextBlock(text=text))
        self.stage_user_message(message_id=user_msg.id, text=text)

        app = cast("CairnApp", self.app)
        orchestrator = app.orchestrator
        session_id = self._session.id

        async def _drive() -> None:
            async for _event in orchestrator.run_turn(session_id, user_msg):
                pass

        self._activity.set_thinking()
        self.run_worker(
            _drive(),
            name=f"turn-{user_msg.id}",
            exclusive=False,
            exit_on_error=False,
        )

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
        # Visible text begins → seal any in-flight thinking row so the
        # elapsed-time counter freezes and the header flips to past tense.
        self._seal_pending_thinking()
        view = self._chat_log.find_message(event.message_id)
        if view is None:
            view = MessageView(message_id=event.message_id, role="assistant")
            self._chat_log.append_message(view)
        view.append_text(event.text)
        self._chat_log.follow_tail()
        if self._activity.state == "thinking":
            self._activity.set_streaming()

    def append_thinking_delta(self, event: AssistantThinkingDelta) -> None:
        """Append reasoning text to a `ThinkingRow`, mounting one on first delta.

        Cardinality is one row per *contiguous* thinking phase per
        assistant message — the screen tracks the in-flight row in
        `_pending_thinking_row` and seals it when a non-thinking
        event arrives (text delta, tool plan, message complete).
        """
        row = self._pending_thinking_row
        if row is None:
            row = ThinkingRow()
            self._pending_thinking_row = row
            self._chat_log.append_thinking_row(row)
        row.append_delta(event.text)
        self._chat_log.follow_tail()

    def finalise_assistant_message(self, event: AssistantMessageComplete) -> None:
        """Mark an assistant message as sealed (no more deltas)."""
        self._seal_pending_thinking()
        view = self._chat_log.find_message(event.message_id)
        if view is not None:
            view.seal()

    def _seal_pending_thinking(self) -> None:
        """Stop the in-flight thinking row's timer and forget the handle.

        Called by any non-thinking event hook (text delta, tool plan,
        message complete). Idempotent.
        """
        row = self._pending_thinking_row
        if row is None:
            return
        row.seal()
        self._pending_thinking_row = None

    def finalise_turn(self, event: TurnComplete) -> None:
        """Per-turn wrap-up hook — stop the activity indicator and
        refresh the cost meter from the injected `cost_source` (when
        bootstrap supplied one)."""
        del event
        self._activity.set_idle()
        self._refresh_cost_from_source()
        self._flush_pending_reload()

    def _refresh_cost_from_source(self) -> None:
        """Spawn a worker that reads the authoritative session cost
        and updates the meter. No-op when no source is configured
        (test harnesses with mock orchestrators)."""
        source = self._cost_source
        if source is None:
            return

        async def _load() -> None:
            try:
                cost = await source()
            except Exception:  # noqa: BLE001 — cost refresh must never break a turn
                return
            self._cost_meter.set_cost(cost)

        self.run_worker(
            _load(),
            name="refresh-cost",
            exclusive=False,
            exit_on_error=False,
        )

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
        # A tool plan also ends a thinking phase (model decided to act).
        self._seal_pending_thinking()
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
        self._activity.set_tool(event.tool_name)

    def mark_tool_completed(self, event: ToolCallCompleted) -> None:
        row = self._chat_log.find_tool_row(event.tool_call_id)
        if row is not None:
            row.mark_completed(
                status=event.status,
                is_error=event.is_error,
                duration_ms=event.duration_ms,
            )
        # After tool completion the LLM usually resumes streaming;
        # fall back to "thinking" so the header shows activity until
        # the first delta arrives.
        if self._activity.state == "tool":
            self._activity.set_thinking()

    # -- Turn-state observer callbacks ----------------------------------

    def show_aborted(self, event: TurnAborted) -> None:
        if event.reason == "turn_timeout":
            # Soft-cancel from the wall-clock watchdog. Tools in flight
            # finished cleanly; the model just didn't get to wrap up.
            text = (
                "◷ turn exceeded the wall-clock deadline and was cancelled. "
                "Tools in flight finished normally."
            )
            self._chat_log.append_banner(Banner(text=text, kind="warning"))
        else:
            detail = event.message or event.reason
            self._chat_log.append_banner(Banner(text=f"✗ turn aborted — {detail}", kind="error"))
        self._activity.set_idle()
        self._flush_pending_reload()

    def show_blocked(self, event: TurnBlocked) -> None:
        self._chat_log.append_banner(
            Banner(text=f"◷ turn blocked — {event.message}", kind="warning")
        )
        self._activity.set_idle()
        self._flush_pending_reload()

    def show_incomplete(self, event: TurnIncomplete) -> None:
        del event
        self._chat_log.append_banner(
            Banner(text="… stopped at max_tokens with a partial tool call", kind="muted")
        )
        self._activity.set_idle()
        self._flush_pending_reload()

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

    def show_delegation_spawned(self, event: DelegationSpawned) -> None:
        """Inline muted line: parent turn just spawned a sub-session."""
        # Short-id surface so the transcript stays scannable; the full
        # session id is one query away in the structured logs.
        short = event.session_id[:8]
        self._chat_log.append_banner(
            Banner(text=f"⤷ delegated to ephemeral session {short}…", kind="muted"),
        )

    def show_delegation_completed(self, event: DelegationCompleted) -> None:
        """Inline muted line: the child session just returned."""
        short = event.session_id[:8]
        self._chat_log.append_banner(
            Banner(text=f"⤴ delegation {short}… returned", kind="muted"),
        )

    def show_drift(self, event: ConfigDriftDetected) -> None:
        """Surface watched-file drift with a muted info banner.

        Run `/reload` to apply the changes.
        """
        counts: dict[str, int] = {}
        for change in event.changes:
            counts[change.category] = counts.get(change.category, 0) + 1
        labels = {
            "config": ("config layer", "config layers"),
            "convention": ("convention file", "convention files"),
            "profile_doc": ("profile doc", "profile docs"),
        }
        parts: list[str] = []
        for category in ("config", "convention", "profile_doc"):
            n = counts.get(category, 0)
            if n == 0:
                continue
            singular, plural = labels[category]
            parts.append(f"{n} {singular if n == 1 else plural}")
        summary = ", ".join(parts) if parts else f"{len(event.changes)} files"
        self._chat_log.append_banner(
            Banner(
                text=f"↻ on-disk changes detected ({summary}) — run /reload to apply",
                kind="muted",
            ),
        )

    # -- Public helpers (for command handlers + bootstrap) --------------

    def append_banner(self, banner: Banner) -> None:
        """Mount an ad-hoc banner in the chat log."""
        self._chat_log.append_banner(banner)

    @property
    def current_cost_usd(self) -> float:
        """Current cost reading on the cost meter."""
        return self._cost_meter.cost_usd

    def is_turn_active(self) -> bool:
        """True while a turn is mid-flight on this screen.

        The activity indicator is the canonical signal: anything other
        than "idle" means the orchestrator is still working
        (thinking / streaming / tool execution).
        """
        return self._activity.state != "idle"

    def queue_reload(self) -> None:
        """Defer a `/reload` until the current turn finishes."""
        self._pending_reload = True

    def _flush_pending_reload(self) -> None:
        """If `/reload` was deferred during a turn, run it now."""
        if not getattr(self, "_pending_reload", False):
            return
        self._pending_reload = False
        app = cast("CairnApp", self.app)
        reloader = app.reloader
        if reloader is None:
            return

        async def _do_reload() -> None:
            result = await reloader.reload()
            kind = "muted" if result.ok else "warning"
            text = result.summary if result.ok else f"reload failed — {result.error}"
            self._chat_log.append_banner(Banner(text=text, kind=kind))
            if result.ok and result.primary_model_drift is not None:
                active, new = result.primary_model_drift
                self._chat_log.append_banner(
                    Banner(
                        text=(
                            f"primary role now resolves to {new}; the active "
                            f"session stays on {active}. Restart cairn to "
                            f"switch."
                        ),
                        kind="muted",
                    ),
                )

        self.run_worker(
            _do_reload(),
            name="deferred-reload",
            exclusive=False,
            exit_on_error=False,
        )

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

    @property
    def _activity(self) -> ActivityIndicator:
        return self.query_one(ActivityIndicator)

    @property
    def _completion(self) -> CompletionMenu:
        return self.query_one(CompletionMenu)

    _staged_user: dict[str, MessageView]
    _pending_thinking_row: ThinkingRow | None

    def on_mount(self) -> None:
        self._staged_user = {}
        self._pending_thinking_row = None
        bar = self.query_one(CommandBar)
        bar.focus()
        app = cast("CairnApp", self.app)
        store = app.prompt_history_store
        if store is not None:
            bar.set_prompt_history(store.load())
        resumed = app.take_resumed_turn_count()
        if resumed > 0:
            noun = "turn" if resumed == 1 else "turns"
            self._chat_log.append_banner(
                Banner(
                    text=f"↺ recovered {resumed} aborted {noun} from previous run",
                    kind="muted",
                )
            )

    def _persist_history(self, bar: CommandBar) -> None:
        """Write the bar's in-memory history to disk if a store is wired."""
        app = cast("CairnApp", self.app)
        store = app.prompt_history_store
        if store is None:
            return
        store.save(bar.prompt_history)
