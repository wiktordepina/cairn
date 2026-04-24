"""Unit tests for `TextualUIEventObserver` — routing only, no app loop.

The observer's job is narrow: translate each `UIEvent` into a
method call on the current session screen. These tests use a
lightweight `_RecordingApp` (see `conftest.py`) so the Textual
loop cost isn't paid per case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from cairn.domain import (
    AssistantMessageComplete,
    AssistantTextDelta,
    BudgetOverflowAdvisory,
    BudgetWarning,
    HistoryCompacted,
    StopReason,
    ToolCallApproved,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallRejected,
    ToolCallStarted,
    ToolCallStatus,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
    TurnIncomplete,
    UserMessagePersisted,
)
from cairn.ui._observer import TextualUIEventObserver

if TYPE_CHECKING:
    from cairn.ui._app import CairnApp


@dataclass
class _FakeScreen:
    """Screen stand-in that records method invocations."""

    append_user_message_calls: list[UserMessagePersisted] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    append_delta_calls: list[AssistantTextDelta] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    finalise_assistant_calls: list[AssistantMessageComplete] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    finalise_turn_calls: list[TurnComplete] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_budget_warning_calls: list[BudgetWarning] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    note_tool_plan_calls: list[ToolCallPlanned] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    mark_tool_approved_calls: list[ToolCallApproved] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    mark_tool_rejected_calls: list[ToolCallRejected] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    mark_tool_started_calls: list[ToolCallStarted] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    mark_tool_completed_calls: list[ToolCallCompleted] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_aborted_calls: list[TurnAborted] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_blocked_calls: list[TurnBlocked] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_incomplete_calls: list[TurnIncomplete] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_compaction_calls: list[HistoryCompacted] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    show_overflow_calls: list[BudgetOverflowAdvisory] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def append_user_message(self, event: UserMessagePersisted) -> None:
        self.append_user_message_calls.append(event)

    def append_delta(self, event: AssistantTextDelta) -> None:
        self.append_delta_calls.append(event)

    def finalise_assistant_message(self, event: AssistantMessageComplete) -> None:
        self.finalise_assistant_calls.append(event)

    def finalise_turn(self, event: TurnComplete) -> None:
        self.finalise_turn_calls.append(event)

    def show_budget_warning(self, event: BudgetWarning) -> None:
        self.show_budget_warning_calls.append(event)

    def note_tool_plan(self, event: ToolCallPlanned) -> None:
        self.note_tool_plan_calls.append(event)

    def mark_tool_approved(self, event: ToolCallApproved) -> None:
        self.mark_tool_approved_calls.append(event)

    def mark_tool_rejected(self, event: ToolCallRejected) -> None:
        self.mark_tool_rejected_calls.append(event)

    def mark_tool_started(self, event: ToolCallStarted) -> None:
        self.mark_tool_started_calls.append(event)

    def mark_tool_completed(self, event: ToolCallCompleted) -> None:
        self.mark_tool_completed_calls.append(event)

    def show_aborted(self, event: TurnAborted) -> None:
        self.show_aborted_calls.append(event)

    def show_blocked(self, event: TurnBlocked) -> None:
        self.show_blocked_calls.append(event)

    def show_incomplete(self, event: TurnIncomplete) -> None:
        self.show_incomplete_calls.append(event)

    def show_compaction(self, event: HistoryCompacted) -> None:
        self.show_compaction_calls.append(event)

    def show_overflow_advisory(self, event: BudgetOverflowAdvisory) -> None:
        self.show_overflow_calls.append(event)


@pytest.fixture
def fake_screen(recording_app: Any) -> _FakeScreen:
    screen = _FakeScreen()
    recording_app.current_session_screen = screen
    return screen


def _observer(recording_app: Any) -> TextualUIEventObserver:
    return TextualUIEventObserver(cast("CairnApp", recording_app))


class TestObserverRouting:
    def test_user_message_persisted_routes_to_append_user_message(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = UserMessagePersisted(message_id="msg-1", turn_id="t-1")
        observer.observe(event)
        assert fake_screen.append_user_message_calls == [event]

    def test_assistant_text_delta_routes_to_append_delta(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = AssistantTextDelta(message_id="msg-2", turn_id="t-1", text="hi")
        observer.observe(event)
        assert fake_screen.append_delta_calls == [event]

    def test_assistant_message_complete_routes_to_finalise_assistant(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = AssistantMessageComplete(message_id="msg-2", turn_id="t-1")
        observer.observe(event)
        assert fake_screen.finalise_assistant_calls == [event]

    def test_turn_complete_routes_to_finalise_turn(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = TurnComplete(session_id="sess-1", turn_id="t-1", stop_reason=StopReason.END_TURN)
        observer.observe(event)
        assert fake_screen.finalise_turn_calls == [event]

    def test_budget_warning_routes_to_show_budget_warning(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = BudgetWarning(session_id="sess-1", turn_id="t-1", cost_usd=4.2, threshold_usd=4.0)
        observer.observe(event)
        assert fake_screen.show_budget_warning_calls == [event]

    def test_tool_call_planned_routes_to_note_tool_plan(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = ToolCallPlanned(tool_call_id="tc-1", turn_id="t-1", tool_name="grep", args={})
        observer.observe(event)
        assert fake_screen.note_tool_plan_calls == [event]

    def test_tool_call_approved_routes_to_mark_tool_approved(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = ToolCallApproved(tool_call_id="tc-1", turn_id="t-1", approved_by="user")
        observer.observe(event)
        assert fake_screen.mark_tool_approved_calls == [event]

    def test_tool_call_rejected_routes_to_mark_tool_rejected(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = ToolCallRejected(
            tool_call_id="tc-1", turn_id="t-1", decided_by="user", reason="no"
        )
        observer.observe(event)
        assert fake_screen.mark_tool_rejected_calls == [event]

    def test_tool_call_started_routes_to_mark_tool_started(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = ToolCallStarted(tool_call_id="tc-1", turn_id="t-1", tool_name="grep")
        observer.observe(event)
        assert fake_screen.mark_tool_started_calls == [event]

    def test_tool_call_completed_routes_to_mark_tool_completed(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = ToolCallCompleted(
            tool_call_id="tc-1",
            turn_id="t-1",
            status=ToolCallStatus.COMPLETED,
            is_error=False,
            duration_ms=12,
        )
        observer.observe(event)
        assert fake_screen.mark_tool_completed_calls == [event]

    def test_turn_aborted_routes_to_show_aborted(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = TurnAborted(session_id="sess-1", turn_id="t-1", reason="user_cancel")
        observer.observe(event)
        assert fake_screen.show_aborted_calls == [event]

    def test_turn_blocked_routes_to_show_blocked(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = TurnBlocked(session_id="sess-1", turn_id="t-1", reason="budget", message="cap")
        observer.observe(event)
        assert fake_screen.show_blocked_calls == [event]

    def test_turn_incomplete_routes_to_show_incomplete(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = TurnIncomplete(session_id="sess-1", turn_id="t-1")
        observer.observe(event)
        assert fake_screen.show_incomplete_calls == [event]

    def test_history_compacted_routes_to_show_compaction(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = HistoryCompacted(
            session_id="sess-1",
            turn_id="t-1",
            blocks_dropped=1,
            messages_dropped=2,
            tokens_before=10,
            tokens_after=5,
            reason="budget",
        )
        observer.observe(event)
        assert fake_screen.show_compaction_calls == [event]

    def test_overflow_advisory_routes_to_show_overflow(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        observer = _observer(recording_app)
        event = BudgetOverflowAdvisory(
            session_id="sess-1",
            turn_id="t-1",
            tokens_projected=210_000,
            context_window=200_000,
            safety_margin=5_000,
            overflow_tokens=10_000,
            will_fit_context_window=False,
        )
        observer.observe(event)
        assert fake_screen.show_overflow_calls == [event]


class TestObserverRobustness:
    def test_no_screen_mounted_silent_drop(self, recording_app: Any) -> None:
        # No screen set; should not raise or touch any callbacks.
        observer = _observer(recording_app)
        observer.observe(UserMessagePersisted(message_id="msg-1", turn_id="t-1"))
        assert recording_app.current_session_screen is None

    def test_unhandled_event_silent_noop(
        self, recording_app: Any, fake_screen: _FakeScreen
    ) -> None:
        """Events without a case branch (yet) must not crash routing.

        Tranche 1 handles only four events; the remaining 15+ land in
        follow-up commits. Forward-compat matters for the intermediate
        review commits on this branch.
        """
        from cairn.domain import SessionCreated

        observer = _observer(recording_app)
        observer.observe(SessionCreated(session_id="sess-1"))
        # No screen method was called.
        assert fake_screen.append_user_message_calls == []
        assert fake_screen.append_delta_calls == []

    def test_screen_method_raising_is_logged_not_propagated(
        self, recording_app: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _Boom:
            def append_delta(self, event: AssistantTextDelta) -> None:
                raise RuntimeError("widget gone")

        recording_app.current_session_screen = _Boom()
        observer = _observer(recording_app)
        with caplog.at_level("ERROR", logger="cairn.ui._observer"):
            observer.observe(AssistantTextDelta(message_id="msg-2", turn_id="t-1", text="hi"))
        assert "failed to route" in caplog.text.lower()
