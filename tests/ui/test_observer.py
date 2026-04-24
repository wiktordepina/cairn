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
    StopReason,
    TurnComplete,
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

    def append_user_message(self, event: UserMessagePersisted) -> None:
        self.append_user_message_calls.append(event)

    def append_delta(self, event: AssistantTextDelta) -> None:
        self.append_delta_calls.append(event)

    def finalise_assistant_message(self, event: AssistantMessageComplete) -> None:
        self.finalise_assistant_calls.append(event)

    def finalise_turn(self, event: TurnComplete) -> None:
        self.finalise_turn_calls.append(event)


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
