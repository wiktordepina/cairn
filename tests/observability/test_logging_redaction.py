"""Tests for `cairn.logging.RedactingFilter` and `setup_logging` redaction wiring."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import pytest

from cairn.logging import RedactingFilter, setup_logging

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_cairn_logger():
    logger = logging.getLogger("cairn")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers.clear()
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            logger.addHandler(handler)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def _make_record(
    msg: str,
    *args: object,
    extra: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="cairn.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=tuple(args) if args else None,
        exc_info=None,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


class TestRedactingFilter:
    def test_redacts_bearer_in_message(self) -> None:
        record = _make_record("Authorization: Bearer abc123def456ghi789jkl012")
        RedactingFilter().filter(record)
        assert record.msg == "Authorization: Bearer [REDACTED]"

    def test_redacts_openai_key_in_args(self) -> None:
        record = _make_record("using key %s", "sk-abc123def456ghi789jkl01234567890")
        RedactingFilter().filter(record)
        assert record.args is not None
        assert "[REDACTED]" in record.args[0]
        assert "sk-abc" not in record.args[0]

    def test_redacts_aws_key_in_extra_string(self) -> None:
        record = _make_record("turn complete", extra={"token": "AKIAIOSFODNN7EXAMPLE"})
        RedactingFilter().filter(record)
        assert record.token == "[REDACTED]"  # type: ignore[attr-defined]

    def test_redacts_anthropic_key_in_nested_extra_dict(self) -> None:
        record = _make_record(
            "turn",
            extra={
                "event": {
                    "tool_name": "web_fetch",
                    "headers": {"x-key": "sk-ant-api03-abc123def456ghi789jkl012mno"},
                }
            },
        )
        RedactingFilter().filter(record)
        event = record.event  # type: ignore[attr-defined]
        assert "[REDACTED]" in event["headers"]["x-key"]
        assert "sk-ant" not in event["headers"]["x-key"]

    def test_returns_true_to_allow_emit(self) -> None:
        record = _make_record("plain text")
        assert RedactingFilter().filter(record) is True

    def test_leaves_innocent_text_alone(self) -> None:
        record = _make_record("the quick brown fox")
        RedactingFilter().filter(record)
        assert record.msg == "the quick brown fox"

    def test_does_not_touch_reserved_logrecord_fields(self) -> None:
        # `name`, `pathname`, etc. would break formatting if rewritten.
        record = _make_record("Bearer abc123def456ghi789jkl012")
        original_name = record.name
        original_pathname = record.pathname
        RedactingFilter().filter(record)
        assert record.name == original_name
        assert record.pathname == original_pathname


class TestSetupLoggingRedactionWiring:
    def test_redaction_enabled_by_default(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file)

        logging.getLogger("cairn").info("Authorization: Bearer abc123def456ghi789jkl012")

        for handler in logging.getLogger("cairn").handlers:
            handler.flush()
        text = log_file.read_text()
        assert "[REDACTED]" in text
        assert "abc123def456" not in text

    def test_kwarg_disables_redaction(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file, redact=False)

        logging.getLogger("cairn").info("Authorization: Bearer abc123def456ghi789jkl012")

        for handler in logging.getLogger("cairn").handlers:
            handler.flush()
        text = log_file.read_text()
        assert "abc123def456ghi789jkl012" in text

    def test_env_disables_redaction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CAIRN_LOG_REDACT", "0")
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file)

        logging.getLogger("cairn").info("Bearer abc123def456ghi789jkl012")

        for handler in logging.getLogger("cairn").handlers:
            handler.flush()
        assert "abc123def456ghi789jkl012" in log_file.read_text()

    def test_kwarg_beats_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAIRN_LOG_REDACT", "0")
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file, redact=True)

        logging.getLogger("cairn").info("Bearer abc123def456ghi789jkl012")

        for handler in logging.getLogger("cairn").handlers:
            handler.flush()
        assert "[REDACTED]" in log_file.read_text()

    def test_filter_attached_to_handler_not_logger(self, tmp_path: Path) -> None:
        # Handler-level so propagated child-logger records are also filtered.
        setup_logging(log_file=tmp_path / "cairn.log")

        logger = logging.getLogger("cairn")
        handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
        assert any(isinstance(f, RedactingFilter) for f in handler.filters)

    def test_filter_catches_propagated_records(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file)

        logging.getLogger("cairn.events").info("Bearer abc123def456ghi789jkl012")

        for handler in logging.getLogger("cairn").handlers:
            handler.flush()
        text = log_file.read_text()
        assert "[REDACTED]" in text
        assert "abc123def456" not in text

    def test_idempotent_does_not_stack_filters(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cairn.log"
        setup_logging(log_file=log_file)
        setup_logging(log_file=log_file)

        logger = logging.getLogger("cairn")
        handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
        filters = [f for f in handler.filters if isinstance(f, RedactingFilter)]
        assert len(filters) == 1
