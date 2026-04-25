"""Logging bootstrap for the `cairn` namespace logger.

Call `setup_logging()` once at process start — before any other cairn
module does real work. Configures a rotating file handler on the
`cairn` namespace logger with `propagate = False` so log lines never
reach stderr: the Textual UI owns the terminal, and stray log output
would corrupt the screen.

A `RedactingFilter` is installed on the namespace logger by default
so any API-key-shaped string in a log message is replaced with
`[REDACTED]` before it hits disk. The filter shares its pattern set
with `cairn.tools.SecretRedactor` via `cairn._redaction_patterns`.

Environment variables:
    CAIRN_LOG_LEVEL: Logging threshold (DEBUG/INFO/WARNING/ERROR/
        CRITICAL). Defaults to `INFO`.
    CAIRN_LOG_FILE: Absolute path to the log file. Defaults to
        `<platformdirs user log dir>/cairn.log`.
    CAIRN_LOG_REDACT: ``0`` / ``false`` / ``no`` to disable redaction.
        Anything else (or unset) enables it. Default: enabled.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_path

from cairn._redaction_patterns import redact_text

__all__ = ["RedactingFilter", "setup_logging"]

_LOGGER_NAME = "cairn"
_DEFAULT_LEVEL = "INFO"
_DEFAULT_FILENAME = "cairn.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RedactingFilter(logging.Filter):
    """Scrubs API-key-shaped strings from log records.

    Attached to the `RotatingFileHandler` by `setup_logging()` rather
    than to the namespace logger directly — handler-level filters run
    on every record reaching the handler, including those propagated
    up from descendant loggers (`cairn.events`, `cairn.orchestrator`,
    …). Logger-level filters are only consulted on the originating
    logger and would miss propagated records.

    Walks `record.msg`, every element of `record.args`, and every
    string value reachable via `record.__dict__` so structured `extra`
    payloads are scrubbed alongside plain message text.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = _redact_args(record.args)
        for key, value in list(record.__dict__.items()):
            if key in _RECORD_RESERVED:
                continue
            if isinstance(value, str):
                record.__dict__[key] = redact_text(value)
            elif isinstance(value, dict):
                record.__dict__[key] = _redact_mapping(value)
        return True


# stdlib LogRecord attributes we must not touch — rewriting them would
# break formatting (e.g. `name`, `levelname`, `pathname`).
_RECORD_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "asctime",
        "message",
    }
)


def _redact_args(args: object) -> object:
    if isinstance(args, tuple):
        return tuple(_redact_value(a) for a in args)
    if isinstance(args, dict):
        return _redact_mapping(args)
    return args


def _redact_mapping(mapping: dict[object, object]) -> dict[object, object]:
    return {k: _redact_value(v) for k, v in mapping.items()}


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, (list, tuple)):
        redacted = [_redact_value(v) for v in value]
        return tuple(redacted) if isinstance(value, tuple) else redacted
    return value


def setup_logging(
    *,
    level: str | int | None = None,
    log_file: Path | str | None = None,
    redact: bool | None = None,
) -> Path:
    """Configure the `cairn` namespace logger.

    Idempotent — a second call replaces any existing cairn handlers
    and re-installs the redaction filter, so tests and long-running
    processes can reconfigure freely.

    Args:
        level: Override for the `CAIRN_LOG_LEVEL` env var. Accepts a
            stdlib level name (``"DEBUG"``) or an integer threshold.
        log_file: Override for the `CAIRN_LOG_FILE` env var. Parent
            directory is created with mode `0o700` if missing.
        redact: Override for the `CAIRN_LOG_REDACT` env var. ``True``
            installs the redaction filter (default), ``False`` skips
            it.

    Returns:
        The resolved log file path.
    """

    resolved_level = _resolve_level(level)
    resolved_path = _resolve_path(log_file)
    resolved_redact = _resolve_redact(redact)

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
    if resolved_redact:
        handler.addFilter(RedactingFilter())
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


def _resolve_redact(override: bool | None) -> bool:
    if override is not None:
        return override
    env = os.environ.get("CAIRN_LOG_REDACT")
    if env is None:
        return True
    return env.strip().lower() not in {"0", "false", "no", "off"}
