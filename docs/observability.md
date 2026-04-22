# Observability

Cairn writes logs to a rotating file on the `cairn` namespace logger,
and (optionally) uses the operating system's trust store for HTTPS
instead of Python's bundled CA list. Both are wired in via small
bootstrap modules that callers invoke once at process start.

!!! note "Tranche 1 — bootstrap only"
    This page describes what has shipped today: the `setup_logging()`
    and `setup_ssl()` entry points. A later tranche will add log
    redaction, tool-call lifecycle logging, and a structured
    `UIEventObserver` implementation — that work lands alongside the
    UI brick.

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

### API

See [Logging](reference/logging.md) for the generated API reference.

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
