"""Shared fixtures for UI-layer tests.

Tranche 1 uses two patterns:

- *Unit* tests exercise the `TextualUIEventObserver` + widget logic
  without spinning up the Textual event loop, using `_RecordingApp`
  (a drop-in with just the observer's required surface).
- *Pilot* tests use `CairnApp.run_test()` with a fake orchestrator
  to drive widget state changes through the real app lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from cairn.domain._enums import SessionType
from cairn.domain._sessions import Session

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _RecordingApp:
    """Minimal stand-in for `CairnApp` used by observer unit tests."""

    current_session_screen: Any = None


@pytest.fixture
def recording_app() -> _RecordingApp:
    return _RecordingApp()


@pytest.fixture
def companion_session() -> Session:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    return Session(
        id="sess-1",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def _restore_cairn_log_propagation() -> Iterator[None]:
    """Keep `cairn.*` loggers visible to pytest's `caplog`.

    `cairn.logging.setup_logging()` sets `propagate=False` on the
    `cairn` namespace logger so the UI can own the terminal. Tests
    that exercise `setup_logging()` (the CLI smoke tests) carry
    that state forward to every later test in the process, which
    blocks `caplog` from seeing `cairn.*` log records. Restore
    propagation per test.
    """
    logger = logging.getLogger("cairn")
    logger.propagate = True
    yield
