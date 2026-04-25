"""Shared fixtures for CLI tests.

The dispatch tests call `main([])`, which triggers
`cairn.logging.setup_logging()` (it sets `propagate=False` on the
`cairn` namespace logger so the UI can own the terminal). That
state would otherwise carry forward to every later test in the
process and block `caplog` from seeing `cairn.*` log records.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _restore_cairn_log_propagation() -> Iterator[None]:
    logger = logging.getLogger("cairn")
    logger.propagate = True
    yield
