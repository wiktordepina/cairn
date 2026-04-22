"""Logging bootstrap for the `cairn` namespace logger.

Call `setup_logging()` once at process start — before any other cairn
module does real work. Configures a rotating file handler on the
`cairn` namespace logger with `propagate = False` so log lines never
reach stderr: the Textual UI owns the terminal, and stray log output
would corrupt the screen.

Environment variables:
    CAIRN_LOG_LEVEL: Logging threshold (DEBUG/INFO/WARNING/ERROR/
        CRITICAL). Defaults to `INFO`.
    CAIRN_LOG_FILE: Absolute path to the log file. Defaults to
        `<platformdirs user log dir>/cairn.log`.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_path

__all__ = ["setup_logging"]

_LOGGER_NAME = "cairn"
_DEFAULT_LEVEL = "INFO"
_DEFAULT_FILENAME = "cairn.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(
    *,
    level: str | int | None = None,
    log_file: Path | str | None = None,
) -> Path:
    """Configure the `cairn` namespace logger.

    Idempotent — a second call replaces any existing cairn handlers,
    so tests and long-running processes can reconfigure freely.

    Args:
        level: Override for the `CAIRN_LOG_LEVEL` env var. Accepts a
            stdlib level name (``"DEBUG"``) or an integer threshold.
        log_file: Override for the `CAIRN_LOG_FILE` env var. Parent
            directory is created with mode `0o700` if missing.

    Returns:
        The resolved log file path.
    """

    resolved_level = _resolve_level(level)
    resolved_path = _resolve_path(log_file)

    resolved_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        resolved_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(resolved_level)
    logger.propagate = False

    return resolved_path


def _resolve_level(override: str | int | None) -> int:
    if isinstance(override, int):
        return override
    name = override if override is not None else os.environ.get("CAIRN_LOG_LEVEL", _DEFAULT_LEVEL)
    return logging.getLevelNamesMapping().get(name.upper(), logging.INFO)


def _resolve_path(override: Path | str | None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("CAIRN_LOG_FILE")
    if env:
        return Path(env).expanduser()
    return user_log_path("cairn", appauthor=False, ensure_exists=False) / _DEFAULT_FILENAME
