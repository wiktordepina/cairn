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

For UI-event lifecycle logging, see `StructuredEventObserver` —
implements the orchestrator's `UIEventObserver` protocol and emits
one structured INFO record per lifecycle event (with the streaming
text deltas dropped entirely).

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
from dataclasses import asdict, fields, is_dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from platformdirs import user_log_path

from cairn._redaction_patterns import redact_text

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from cairn.domain import UIEvent

    EventLogger = logging.Logger | logging.LoggerAdapter[logging.Logger]

__all__ = [
    "RedactingFilter",
    "StructuredEventObserver",
    "make_event_logger",
    "setup_logging",
]

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
            # `record.args` is typed as `tuple | Mapping[str, object] | None`;
            # `_redact_args` preserves the shape, so the cast back is safe.
            record.args = cast(
                "tuple[object, ...] | Mapping[str, object]",
                _redact_args(record.args),
            )
        for key, value in list(record.__dict__.items()):
            if key in _RECORD_RESERVED:
                continue
            if isinstance(value, str):
                record.__dict__[key] = redact_text(value)
            elif isinstance(value, dict):
                record.__dict__[key] = _redact_mapping(cast("dict[object, object]", value))
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
        return tuple(_redact_value(a) for a in cast("tuple[object, ...]", args))
    if isinstance(args, dict):
        return _redact_mapping(cast("dict[object, object]", args))
    return args


def _redact_mapping(mapping: dict[object, object]) -> dict[object, object]:
    return {k: _redact_value(v) for k, v in mapping.items()}


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return _redact_mapping(cast("dict[object, object]", value))
    if isinstance(value, (list, tuple)):
        items = cast("list[object] | tuple[object, ...]", value)
        redacted = [_redact_value(v) for v in items]
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


# ---------------------------------------------------------------------------
# Structured event logging
# ---------------------------------------------------------------------------


_EVENTS_LOGGER_NAME = "cairn.events"


def make_event_logger(profile: str | None) -> logging.LoggerAdapter[logging.Logger]:
    """Return a `LoggerAdapter` over `cairn.events` that injects
    `profile` into every record's `extra` payload.

    Bound at bootstrap time so multi-profile log triage is trivial:
    every structured-event record carries the active profile name.
    Missing profile (e.g. tests, CLI plumbing before profile resolution)
    is recorded as ``"<unbound>"``.
    """

    return _ProfileLoggerAdapter(
        logging.getLogger(_EVENTS_LOGGER_NAME),
        {"profile": profile or "<unbound>"},
    )


class _ProfileLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Adapter that merges its bound `extra` with each call's `extra`
    dict, so the per-call `extra` (event-specific fields) is preserved
    alongside the bound `profile`."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        merged: dict[str, Any] = dict(self.extra or {})
        existing = kwargs.get("extra")
        if isinstance(existing, dict):
            merged.update(cast("dict[str, Any]", existing))
        kwargs["extra"] = merged
        return msg, kwargs


class StructuredEventObserver:
    """`UIEventObserver` that serialises every lifecycle event to a
    structured log record on `cairn.events`.

    Levels:

    - INFO: every variant in the `UIEvent` union except
      `AssistantTextDelta`, which is dropped entirely (per-token noise
      dwarfs every other category, and the assembled message is logged
      on `AssistantMessageComplete`).
    - WARNING: any exception raised while serialising an event. The
      observer never re-raises — fan-out is fire-and-forget per the
      `UIEventObserver` contract.

    Tool-call lifecycle (planned/approved/rejected/started/completed)
    is logged with `tool_name`, `tool_use_id`, `decided_by`, `status`,
    `duration_ms`, `error_class` — but **not** tool input or output.
    Approval decisions are deliberately INFO so a tail of `cairn.log`
    is a viable audit trail alongside the persisted
    `approval_decisions` table.
    """

    def __init__(
        self,
        logger: EventLogger | None = None,
    ) -> None:
        self._log: EventLogger = (
            logger if logger is not None else logging.getLogger(_EVENTS_LOGGER_NAME)
        )

    def observe(self, event: UIEvent) -> None:
        try:
            self._route(event)
        except Exception:  # noqa: BLE001
            # Fall back to the cairn namespace logger — `self._log` may
            # itself be the source of the failure (LoggerAdapter has
            # been observed to raise when `extra` collides with reserved
            # LogRecord attrs). We swallow per the observer contract.
            logging.getLogger("cairn").warning(
                "StructuredEventObserver: failed to route %r",
                type(event).__name__,
                exc_info=True,
            )

    def _route(self, event: UIEvent) -> None:
        # Lazy imports keep this module free of a hard dependency on
        # `cairn.domain` at import time (avoids a domain → logging
        # cycle if domain ever grows logging).
        from cairn.domain import (
            AssistantMessageComplete,
            AssistantTextDelta,
            BudgetOverflowAdvisory,
            BudgetWarning,
            DelegationCompleted,
            DelegationSpawned,
            HistoryCompacted,
            ModelSwapped,
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

        if isinstance(event, AssistantTextDelta):
            return

        match event:
            case UserMessagePersisted():
                self._info(
                    "user_message_persisted: turn=%s msg=%s",
                    event.turn_id,
                    event.message_id,
                    event=event,
                )
            case AssistantMessageComplete():
                self._info(
                    "assistant_message_complete: turn=%s msg=%s",
                    event.turn_id,
                    event.message_id,
                    event=event,
                )
            case ToolCallPlanned():
                self._info(
                    "tool_call_planned: turn=%s tool=%s id=%s",
                    event.turn_id,
                    event.tool_name,
                    event.tool_call_id,
                    event=event,
                    omit={"args"},
                )
            case ToolCallApproved():
                self._info(
                    "tool_call_approved: turn=%s id=%s by=%s",
                    event.turn_id,
                    event.tool_call_id,
                    event.approved_by,
                    event=event,
                )
            case ToolCallRejected():
                self._info(
                    "tool_call_rejected: turn=%s id=%s by=%s reason=%s",
                    event.turn_id,
                    event.tool_call_id,
                    event.decided_by,
                    event.reason,
                    event=event,
                )
            case ToolCallStarted():
                self._info(
                    "tool_call_started: turn=%s tool=%s id=%s",
                    event.turn_id,
                    event.tool_name,
                    event.tool_call_id,
                    event=event,
                )
            case ToolCallCompleted():
                self._info(
                    "tool_call_completed: turn=%s id=%s status=%s is_error=%s duration_ms=%s",
                    event.turn_id,
                    event.tool_call_id,
                    event.status.value,
                    event.is_error,
                    event.duration_ms,
                    event=event,
                )
            case DelegationSpawned():
                self._info(
                    "delegation_spawned: turn=%s sub_session=%s",
                    event.turn_id,
                    event.session_id,
                    event=event,
                )
            case DelegationCompleted():
                self._info(
                    "delegation_completed: turn=%s sub_session=%s",
                    event.turn_id,
                    event.session_id,
                    event=event,
                )
            case ObservationExtractionRequested():
                self._info(
                    "observation_extraction_requested: turn=%s session=%s",
                    event.turn_id,
                    event.session_id,
                    event=event,
                )
            case ObservationExtractionCompleted():
                self._info(
                    "observation_extraction_completed: turn=%s session=%s status=%s written=%s",
                    event.turn_id,
                    event.session_id,
                    event.status,
                    event.observations_written,
                    event=event,
                )
            case TurnComplete():
                self._info(
                    "turn_complete: turn=%s session=%s stop=%s",
                    event.turn_id,
                    event.session_id,
                    event.stop_reason.value,
                    event=event,
                )
            case TurnAborted():
                self._info(
                    "turn_aborted: turn=%s session=%s reason=%s",
                    event.turn_id,
                    event.session_id,
                    event.reason,
                    event=event,
                )
            case TurnBlocked():
                self._info(
                    "turn_blocked: turn=%s session=%s reason=%s",
                    event.turn_id,
                    event.session_id,
                    event.reason,
                    event=event,
                )
            case TurnIncomplete():
                self._info(
                    "turn_incomplete: turn=%s session=%s",
                    event.turn_id,
                    event.session_id,
                    event=event,
                )
            case BudgetWarning():
                self._info(
                    "budget_warning: turn=%s session=%s cost=%.6f threshold=%.6f",
                    event.turn_id,
                    event.session_id,
                    event.cost_usd,
                    event.threshold_usd,
                    event=event,
                )
            case HistoryCompacted():
                self._info(
                    "history_compacted: turn=%s session=%s blocks=%s msgs=%s "
                    "tokens=%s→%s reason=%s",
                    event.turn_id,
                    event.session_id,
                    event.blocks_dropped,
                    event.messages_dropped,
                    event.tokens_before,
                    event.tokens_after,
                    event.reason,
                    event=event,
                )
            case BudgetOverflowAdvisory():
                self._info(
                    "budget_overflow_advisory: turn=%s session=%s overflow=%s fits_window=%s",
                    event.turn_id,
                    event.session_id,
                    event.overflow_tokens,
                    event.will_fit_context_window,
                    event=event,
                )
            case SessionCreated():
                self._info(
                    "session_created: session=%s",
                    event.session_id,
                    event=event,
                )
            case SessionResumed():
                self._info(
                    "session_resumed: session=%s",
                    event.session_id,
                    event=event,
                )
            case SessionArchived():
                self._info(
                    "session_archived: session=%s",
                    event.session_id,
                    event=event,
                )
            case ModelSwapped():
                self._info(
                    "model_swapped: session=%s mode=%s from=%s to=%s",
                    event.session_id,
                    event.mode,
                    event.from_model,
                    event.to_model,
                    event=event,
                )
            case _:
                # Forward-compat: an unknown event variant gets logged
                # without crashing the observer.
                self._log.info(
                    "unknown_ui_event: %s",
                    type(event).__name__,
                    extra={"event_type": type(event).__name__},
                )

    def _info(
        self,
        fmt: str,
        *args: Any,
        event: object,
        omit: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._log.info(fmt, *args, extra=_event_extra(event, omit=omit))


# Keys that exist on every dataclass UIEvent — pulled into the `extra`
# dict at the top level so log filters / formatters can find them
# without parsing the message.
_TOP_LEVEL_EVENT_KEYS = ("turn_id", "session_id", "message_id", "tool_call_id")


def _event_extra(
    event: object,
    *,
    omit: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Build the `extra=` payload for one event.

    Always includes ``event_type``. Promotes every event-specific
    dataclass field that is also a "top-level" key (turn_id, etc.) to
    the top of the dict for easy filtering, then nests the full
    serialised event under ``event``. Fields named in `omit` (e.g.
    `args` on `ToolCallPlanned`) are dropped — we never log tool
    inputs or outputs.
    """

    out: dict[str, Any] = {"event_type": type(event).__name__}
    # `is_dataclass` narrows to "instance OR class"; `asdict` only takes
    # an instance. UIEvent variants are always instances, but make that
    # explicit for the type checker.
    if not is_dataclass(event) or isinstance(event, type):
        return out

    omitted = omit or frozenset()
    for field in fields(event):
        if field.name in omitted:
            continue
        value = getattr(event, field.name)
        if field.name in _TOP_LEVEL_EVENT_KEYS:
            out[field.name] = value

    payload = asdict(event)
    for key in omitted:
        payload.pop(key, None)
    out["event"] = payload
    return out
