"""Tests for `cairn.logging.setup_logging`."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from cairn.logging import setup_logging


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


def test_setup_logging_attaches_rotating_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "cairn.log"

    resolved = setup_logging(log_file=log_file)

    assert resolved == log_file
    logger = logging.getLogger("cairn")
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    assert Path(handlers[0].baseFilename) == log_file


def test_setup_logging_does_not_propagate(tmp_path: Path) -> None:
    setup_logging(log_file=tmp_path / "cairn.log")

    assert logging.getLogger("cairn").propagate is False


def test_setup_logging_writes_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "cairn.log"
    setup_logging(log_file=log_file, level="INFO")

    logging.getLogger("cairn").info("hello world")

    for handler in logging.getLogger("cairn").handlers:
        handler.flush()
    assert "hello world" in log_file.read_text()


def test_setup_logging_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "cairn.log"

    setup_logging(log_file=log_file)
    setup_logging(log_file=log_file)

    logger = logging.getLogger("cairn")
    assert sum(isinstance(h, RotatingFileHandler) for h in logger.handlers) == 1


def test_setup_logging_honours_level_kwarg(tmp_path: Path) -> None:
    setup_logging(log_file=tmp_path / "cairn.log", level="DEBUG")

    assert logging.getLogger("cairn").level == logging.DEBUG


def test_setup_logging_accepts_integer_level(tmp_path: Path) -> None:
    setup_logging(log_file=tmp_path / "cairn.log", level=logging.WARNING)

    assert logging.getLogger("cairn").level == logging.WARNING


def test_setup_logging_env_overrides_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "from-env.log"
    monkeypatch.setenv("CAIRN_LOG_FILE", str(env_path))

    resolved = setup_logging()

    assert resolved == env_path


def test_setup_logging_env_overrides_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAIRN_LOG_LEVEL", "warning")

    setup_logging(log_file=tmp_path / "cairn.log")

    assert logging.getLogger("cairn").level == logging.WARNING


def test_setup_logging_kwarg_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAIRN_LOG_LEVEL", "ERROR")

    setup_logging(log_file=tmp_path / "cairn.log", level="DEBUG")

    assert logging.getLogger("cairn").level == logging.DEBUG


def test_setup_logging_unknown_level_falls_back_to_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAIRN_LOG_LEVEL", "NONSENSE")

    setup_logging(log_file=tmp_path / "cairn.log")

    assert logging.getLogger("cairn").level == logging.INFO


def test_setup_logging_creates_parent_dir_with_restricted_permissions(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nest"
    log_file = nested / "cairn.log"

    setup_logging(log_file=log_file)

    assert nested.exists()
    assert (nested.stat().st_mode & 0o777) == 0o700
