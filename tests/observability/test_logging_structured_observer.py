"""Tests for `cairn.logging.StructuredEventObserver` + `make_event_logger`."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from cairn.domain import (
    AssistantMessageComplete,
    AssistantTextDelta,
    BudgetOverflowAdvisory,
    BudgetWarning,
    DelegationCompleted,
    DelegationSpawned,
    HistoryCompacted,
    ObservationExtractionCompleted,
    ObservationExtractionRequested,
    SessionArchived,
    SessionCreated,
    SessionResumed,
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
from cairn.domain._enums import ErrorClass, StopReason, ToolCallStatus
from cairn.logging import StructuredEventObserver, make_event_logger


@pytest.fixture
def observer() -> StructuredEventObserver:
    return StructuredEventObserver(make_event_logger("home"))


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "cairn.events"]


class TestProfileBinding:
    def test_profile_present_on_every_record(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(SessionCreated(session_id="s1"))

        assert len(_records(caplog)) == 1
        record = _records(caplog)[0]
        assert record.profile == "home"  # type: ignore[attr-defined]

    def test_unbound_profile_falls_back_to_placeholder(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        observer = StructuredEventObserver(make_event_logger(None))
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(SessionCreated(session_id="s1"))

        assert _records(caplog)[0].profile == "<unbound>"  # type: ignore[attr-defined]

    def test_per_call_extra_merges_with_bound_profile(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(UserMessagePersisted(message_id="m1", turn_id="t1"))

        record = _records(caplog)[0]
        assert record.profile == "home"  # type: ignore[attr-defined]
        assert record.event_type == "UserMessagePersisted"  # type: ignore[attr-defined]
        assert record.turn_id == "t1"  # type: ignore[attr-defined]
        assert record.message_id == "m1"  # type: ignore[attr-defined]


class TestDispatch:
    def test_user_message_persisted_logs_at_info(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(UserMessagePersisted(message_id="m1", turn_id="t1"))

        record = _records(caplog)[0]
        assert record.levelno == logging.INFO
        assert "user_message_persisted" in record.getMessage()

    def test_tool_call_planned_omits_args(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        # Tool inputs must never reach the structured log.
        secret_args: dict[str, Any] = {"path": "/etc/passwd", "auth": "Bearer xyz"}
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc1",
                    turn_id="t1",
                    tool_name="file_read",
                    args=secret_args,
                )
            )

        record = _records(caplog)[0]
        # Top-level fields are promoted; args is omitted.
        assert record.tool_call_id == "tc1"  # type: ignore[attr-defined]
        assert "args" not in record.event  # type: ignore[attr-defined]
        # And the message line never names the args either.
        assert "Bearer" not in record.getMessage()
        assert "/etc/passwd" not in record.getMessage()

    def test_tool_call_approved_logs_decided_by_at_info(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                ToolCallApproved(tool_call_id="tc1", turn_id="t1", approved_by="auto")
            )

        record = _records(caplog)[0]
        assert record.levelno == logging.INFO
        assert record.event["approved_by"] == "auto"  # type: ignore[attr-defined]

    def test_tool_call_rejected_logs_decided_by_at_info(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                ToolCallRejected(
                    tool_call_id="tc1",
                    turn_id="t1",
                    decided_by="user",
                    reason="not now",
                )
            )

        record = _records(caplog)[0]
        assert record.levelno == logging.INFO
        assert record.event["decided_by"] == "user"  # type: ignore[attr-defined]

    def test_tool_call_completed_carries_status_and_duration(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                ToolCallCompleted(
                    tool_call_id="tc1",
                    turn_id="t1",
                    status=ToolCallStatus.COMPLETED,
                    is_error=False,
                    duration_ms=42,
                )
            )

        record = _records(caplog)[0]
        assert record.event["duration_ms"] == 42  # type: ignore[attr-defined]
        assert record.event["status"] == "completed"  # type: ignore[attr-defined]

    def test_assistant_text_delta_dropped_entirely(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        # Per-token noise — even DEBUG would flood. Drop on the floor.
        with caplog.at_level(logging.DEBUG, logger="cairn.events"):
            observer.observe(AssistantTextDelta(message_id="m1", turn_id="t1", text="hello"))

        assert _records(caplog) == []

    def test_assistant_message_complete_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(AssistantMessageComplete(message_id="m1", turn_id="t1"))

        assert len(_records(caplog)) == 1
        assert _records(caplog)[0].event_type == "AssistantMessageComplete"  # type: ignore[attr-defined]

    def test_turn_complete_carries_stop_reason(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                TurnComplete(
                    session_id="s1",
                    turn_id="t1",
                    stop_reason=StopReason.END_TURN,
                )
            )

        assert _records(caplog)[0].event["stop_reason"] == "end_turn"  # type: ignore[attr-defined]

    def test_turn_aborted_carries_reason_and_error_class(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                TurnAborted(
                    session_id="s1",
                    turn_id="t1",
                    reason="user_cancel",
                    error_class=ErrorClass.USER,
                )
            )

        record = _records(caplog)[0]
        assert record.event["reason"] == "user_cancel"  # type: ignore[attr-defined]
        assert record.event["error_class"] == "user"  # type: ignore[attr-defined]

    def test_session_lifecycle_events_log(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(SessionCreated(session_id="s1"))
            observer.observe(SessionResumed(session_id="s1"))
            observer.observe(SessionArchived(session_id="s1"))

        types = [r.event_type for r in _records(caplog)]  # type: ignore[attr-defined]
        assert types == ["SessionCreated", "SessionResumed", "SessionArchived"]

    def test_observation_extraction_pair_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(ObservationExtractionRequested(session_id="s1", turn_id="t1"))
            observer.observe(
                ObservationExtractionCompleted(
                    session_id="s1",
                    turn_id="t1",
                    status="succeeded",
                    observations_written=3,
                )
            )

        records = _records(caplog)
        assert len(records) == 2
        assert records[1].event["observations_written"] == 3  # type: ignore[attr-defined]

    def test_delegation_lifecycle_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(DelegationSpawned(session_id="sub1", turn_id="t1"))
            observer.observe(DelegationCompleted(session_id="sub1", turn_id="t1"))

        types = [r.event_type for r in _records(caplog)]  # type: ignore[attr-defined]
        assert types == ["DelegationSpawned", "DelegationCompleted"]

    def test_history_compacted_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                HistoryCompacted(
                    session_id="s1",
                    turn_id="t1",
                    blocks_dropped=12,
                    messages_dropped=3,
                    tokens_before=100_000,
                    tokens_after=50_000,
                    reason="budget",
                )
            )

        record = _records(caplog)[0]
        assert record.event["blocks_dropped"] == 12  # type: ignore[attr-defined]
        assert record.event["reason"] == "budget"  # type: ignore[attr-defined]

    def test_budget_advisory_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                BudgetOverflowAdvisory(
                    session_id="s1",
                    turn_id="t1",
                    tokens_projected=210_000,
                    context_window=200_000,
                    safety_margin=10_000,
                    overflow_tokens=10_000,
                    will_fit_context_window=False,
                )
            )

        assert _records(caplog)[0].event["overflow_tokens"] == 10_000  # type: ignore[attr-defined]

    def test_budget_warning_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                BudgetWarning(
                    session_id="s1",
                    turn_id="t1",
                    cost_usd=4.5,
                    threshold_usd=5.0,
                )
            )

        record = _records(caplog)[0]
        assert record.event["cost_usd"] == 4.5  # type: ignore[attr-defined]

    def test_turn_blocked_and_incomplete_log(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                TurnBlocked(
                    session_id="s1",
                    turn_id="t1",
                    reason="budget",
                    message="cap reached",
                )
            )
            observer.observe(TurnIncomplete(session_id="s1", turn_id="t1"))

        types = [r.event_type for r in _records(caplog)]  # type: ignore[attr-defined]
        assert types == ["TurnBlocked", "TurnIncomplete"]

    def test_tool_call_started_logs_tool_name(
        self,
        caplog: pytest.LogCaptureFixture,
        observer: StructuredEventObserver,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cairn.events"):
            observer.observe(
                ToolCallStarted(tool_call_id="tc1", turn_id="t1", tool_name="file_read")
            )

        record = _records(caplog)[0]
        assert record.event["tool_name"] == "file_read"  # type: ignore[attr-defined]


class TestErrorHandling:
    def test_observer_swallows_exceptions(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A logger that raises on every call. The observer must not propagate.
        broken = logging.getLogger("cairn.events.broken")

        class _Boom(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                raise RuntimeError("boom")

        broken.addHandler(_Boom())
        broken.setLevel(logging.INFO)
        broken.propagate = False

        observer = StructuredEventObserver(broken)

        with caplog.at_level(logging.WARNING, logger="cairn"):
            # Must not raise.
            observer.observe(SessionCreated(session_id="s1"))

        # The fallback warning lands on the cairn logger.
        warns = [
            r
            for r in caplog.records
            if r.name == "cairn" and "StructuredEventObserver" in r.getMessage()
        ]
        assert warns, "expected fallback warning on cairn logger"
        assert warns[0].levelno == logging.WARNING
