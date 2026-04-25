# Observability

Cairn writes logs to a rotating file on the `cairn` namespace logger,
and (optionally) uses the operating system's trust store for HTTPS
instead of Python's bundled CA list. Both are wired in via small
bootstrap modules that callers invoke once at process start.

!!! note "Tranches 1 + 2 — shipped"
    Tranche 1 (`setup_logging` / `setup_ssl`) shipped at 0.7.0.
    Tranche 2 (log redaction, structured event observer, structured
    crash-recovery logs) shipped at 0.12.0 and is described in the
    sections below.

## Logging

`cairn.logging.setup_logging()` configures a single
`RotatingFileHandler` (5 MB × 3 backups) on the `cairn` namespace
logger, sets `propagate = False`, and returns the resolved path.

Why `propagate = False`: the planned Textual UI owns the terminal.
Any stray log line that reaches stderr corrupts the screen. Keeping
cairn's log records off the root logger prevents that. It also means
`logging.getLogger("cairn.something")` records land in the cairn log
file and nowhere else — if you want them on stderr as well during
development, attach your own handler to the `cairn` logger before or
after the bootstrap call.

### Configuration

Resolution order (highest priority first):

1. Keyword arguments to `setup_logging(level=..., log_file=...)`.
2. Environment variables:
    - `CAIRN_LOG_LEVEL` — `DEBUG` / `INFO` / `WARNING` / `ERROR` /
      `CRITICAL` (case-insensitive). Unknown values fall back to
      `INFO`.
    - `CAIRN_LOG_FILE` — absolute path to the log file.
3. Defaults:
    - Level: `INFO`.
    - Path: `<platformdirs user log dir>/cairn.log`. On Linux that
      resolves under `$XDG_STATE_HOME` or `~/.local/state/cairn/`;
      macOS uses `~/Library/Logs/cairn/`; Windows uses
      `%LOCALAPPDATA%\cairn\Logs\`.

The parent directory is created with mode `0o700` if missing.

### Idempotency

Calling `setup_logging()` a second time removes any handlers installed
by the first call and replaces them. Tests can reconfigure freely; a
future `/reload` command can re-point the log file without restart.

### Log redaction

`setup_logging()` installs a `RedactingFilter` on the rotating handler
by default. The filter rewrites any string that matches an API-key
pattern from `cairn._redaction_patterns` — the same set used by the
tool-output `SecretRedactor` middleware — replacing the secret with
`[REDACTED]` (preserving structural context like a `Bearer ` prefix).

Patterns covered today:

- `Bearer <token>` (Authorization headers, ≥ 20-char token)
- OpenAI-shaped `sk-…`
- Anthropic-shaped `sk-ant-…`
- Google API `AIza…`
- AWS access key `AKIA…`

The filter walks `record.msg`, every element of `record.args`, and
every string value in the record's `extra` payload (recursing into
nested dicts and lists). Reserved `LogRecord` attributes (`name`,
`pathname`, `levelname`, …) are left alone so formatters keep working.

Redaction is **best-effort** — it cannot know about secrets that don't
match the pattern set, and it is not a substitute for not-logging-
secrets in the first place. Treat the cairn log file as sensitive.

The filter is attached to the **handler**, not the namespace logger.
This matters because logger-level filters are only consulted on the
originating logger; handler-level filters run on every record reaching
the handler, including those propagated up from descendant loggers
(`cairn.events`, `cairn.orchestrator`, …).

#### Opt-out

Resolution order (highest priority first):

1. `setup_logging(redact=False)` keyword argument.
2. `CAIRN_LOG_REDACT` environment variable. Set to `0`, `false`,
   `no`, or `off` to disable. Anything else (or unset) keeps redaction
   on.
3. Default: enabled.

Disable only if you're debugging the filter itself or running in an
environment where false positives from the patterns are causing real
log loss. The shape of an API key is rarely a false positive.

## Structured event log

`cairn.logging.StructuredEventObserver` implements the orchestrator's
`UIEventObserver` protocol and emits one `INFO` record per
`UIEvent` lifecycle event on the `cairn.events` logger. The bootstrap
attaches it alongside `TextualUIEventObserver`, so every turn produces
both the on-screen widget updates and a tail-able structured log.

Use it for bug reports — the most recent N lines of `cairn.log` are
usually enough to reconstruct what happened during a misbehaving turn,
and the `event_type`, `turn_id`, `session_id`, `tool_call_id`, and
`message_id` fields are promoted to the top of each record's `extra`
payload for easy filtering.

Sample log lines (formatter elided):

```
INFO cairn.events session_created: session=01h…
INFO cairn.events user_message_persisted: turn=01h… msg=01h…
INFO cairn.events tool_call_planned: turn=01h… tool=file_read id=tc_…
INFO cairn.events tool_call_approved: turn=01h… id=tc_… by=auto
INFO cairn.events tool_call_started: turn=01h… tool=file_read id=tc_…
INFO cairn.events tool_call_completed: turn=01h… id=tc_… status=completed is_error=False duration_ms=12
INFO cairn.events assistant_message_complete: turn=01h… msg=01h…
INFO cairn.events turn_complete: turn=01h… session=01h… stop=end_turn
```

The full event payload (every dataclass field) lands under the
`event` key in `extra`, so a JSON formatter can pick it up later
without parsing the message line.

### Drift events

The file watcher (0.15.0) emits `ConfigDriftDetected` once per
drift transition. The structured observer turns each into an
`INFO` record on `cairn.events`:

```
INFO cairn.events config_drift_detected: count=2 categories=config,convention
```

Per-path detail (the full `changes` tuple) lands under the
`event` key in `extra` for filtering. Use this to confirm
which file Cairn noticed change without having to scrape the UI
banner.

### What is and isn't logged

Logged at `INFO`:

- Every variant in the `UIEvent` union except `AssistantTextDelta`.
- Approval decisions (`ToolCallApproved`, `ToolCallRejected`) carry
  the `decided_by` field — `auto` for middleware decisions, `user` for
  modal decisions — so the log is a viable audit trail alongside the
  persisted `approval_decisions` table.
- Tool-call lifecycle records carry `tool_name`, `tool_call_id`,
  `status`, `is_error`, and `duration_ms` — but **never tool input or
  output**.

Not logged:

- `AssistantTextDelta` is dropped entirely. Per-token noise dwarfs
  every other category, and the assembled message is logged on
  `AssistantMessageComplete`.
- Tool-call arguments (`args` on `ToolCallPlanned`) and tool result
  bodies. The argument set or output may contain secrets, file
  contents, or PII; routing them to the log file is a regression even
  with the redaction filter on.

### Multi-profile triage

`make_event_logger(profile_name)` returns a `LoggerAdapter` over
`cairn.events` that injects `profile` into every record's `extra`
payload. The bootstrap binds the active profile name; multi-profile
log triage becomes a `grep profile=<name> cairn.log`. Records logged
without a bound profile (tests, plumbing called before profile
resolution) carry `profile=<unbound>`.

### Error handling

If routing an event raises (e.g. an unexpected `LogRecord` `extra`
collision), the observer falls back to a `WARNING` on the namespace
`cairn` logger and continues. It never re-raises — the
`UIEventObserver` contract is fire-and-forget.

## Crash-recovery logs

`Orchestrator.resume_aborted_turns()` is called once at boot to mark
turns left non-terminal by a prior process crash. It emits three
record kinds on `cairn.orchestrator`:

- `resume_aborted_turns.scan_started` — entry, no fields.
- `turn.resumed_as_aborted` — one record per dangling turn, with
  `turn_id`, `session_id`, `prior_state` (the state the turn was
  stuck in), and the canonical `reason="process_crash"`.
- `resume_aborted_turns.scan_complete` — exit, with
  `resumed_count` so a single tail of `cairn.log` shows the sweep
  result.

The UI also surfaces the count via a muted info banner on first
mount (see `docs/ui.md`).

### API

See [Logging](reference/logging.md) for the generated API reference.

## Cache usage in `model_usage`

Every persisted `model_usage` row carries `cache_read_tokens` and
`cache_write_tokens` populated from the streamed `UsageEvent`. The
two fields make prompt-cache effectiveness queryable directly:

```sql
SELECT operation,
       input_tokens,
       output_tokens,
       cache_read_tokens,
       cache_write_tokens,
       cost_usd
FROM model_usage
WHERE session_id = ?
ORDER BY recorded_at;
```

`cache_write_tokens` is always zero for OpenAI and DeepSeek — those
APIs do not separate cache creation from baseline input. Only
Anthropic (and OpenRouter on Anthropic-backed routes) reports both.
See [Prompt caching](prompt-caching.md) for the full provider
breakdown.

## TLS trust

`cairn.ssl.setup_ssl()` injects the operating system's trust store
into Python's `ssl` module via the [truststore](https://pypi.org/project/truststore/)
package. The practical need is corporate MITM proxies (Netskope,
Zscaler, etc.) that rewrite provider HTTPS endpoints with a private
CA — Python's default `certifi` bundle doesn't trust that CA, so
provider SDK calls fail certificate validation.

`setup_ssl()` is deliberately silent on failure: if `truststore` is
not installed, or `truststore.inject_into_ssl()` raises, the function
logs at `DEBUG` and returns `False`. The rest of the application
continues with Python's default `ssl` context. Nothing crashes over
an optional bootstrap.

### When to call

Once, at the top of `main()`, before any HTTPS client is constructed.
Calling after an HTTPS connection has already been opened does not
retroactively change that connection's trust context.

### Idempotency

A second call after a successful injection returns `True` without
re-invoking `truststore`. A second call after a failed first call
will retry, so transient import failures don't latch to `False`.

### API

See [SSL](reference/ssl.md) for the generated API reference.
