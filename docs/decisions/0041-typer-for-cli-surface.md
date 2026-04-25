# 0041 — Typer for the CLI surface

**Date:** 2026-04-25
**Status:** accepted

## Context

Through 0.13.x, `cairn`'s console script was a single argparse-based
file under `src/cairn/cli.py` with one job: parse `--profile` /
`--version` and hand off to the Textual launch. The CLI brick
(0.14.0) ships nested subcommands —
`cairn config secret set <ref>`, `cairn trust add [PATH]`,
`cairn config init` — that argparse handles awkwardly:

- Nested `add_subparsers(dest=...)` chains require manual
  dispatch boilerplate at every level.
- Sharing `--profile` across subcommands needs a parent parser per
  level or repeated `add_argument` calls.
- `argparse.parse_args` raises `SystemExit` for `--help` and
  validation errors, which conflicts with the `int`-returning
  `main()` contract some of the existing tests already relied on.
- Driving subcommands in tests requires running through
  `main(argv)` and catching `SystemExit`.

We considered:

- **Stay on argparse.** Familiar, stdlib, zero new deps. But every
  one of the gripes above multiplies as the subcommand surface
  grows. The dispatch boilerplate would have crowded out the
  actual subcommand logic.
- **Click directly.** Click is the underlying engine for both
  argparse-replacements that matter (`Typer`, `Cleo`). Decorator-
  based, widely understood. But Typer adds annotation-driven
  option parsing on top of click for free, with no functional
  loss.
- **Typer.** Decorator-based, type-annotation-driven, builds on
  click. `typer.testing.CliRunner` (re-exported from click) makes
  subcommand tests significantly less ceremony than driving
  argparse.

Two new transitive deps either way: `click` is already widely
deployed; Typer adds itself, `shellingham` (for shell-completion
detection — we disable this), and brings in `click >= 8.1`. All
small, MIT-licensed, pure-Python.

## Decision

Migrate the CLI to Typer. The root `cairn.cli` package mounts
sub-apps for `config` (with a nested `secret` sub-app) and
`trust`; each subcommand module exposes a `register(app)` callable
so the dispatch wiring is declarative and easy to audit.

`main(argv) -> int` keeps its existing contract by wrapping
`app(args, standalone_mode=False)` and translating `click`'s
control-flow exceptions (`UsageError`, `Exit`, `Abort`) into
deterministic integer exit codes. This means the
`pyproject.toml`'s `[project.scripts] cairn = "cairn.cli:main"`
declaration doesn't change and existing call sites (tests,
embedded uses) keep working.

A few Typer settings we override deliberately:

- `pretty_exceptions_enable=False` — Typer's rich-traceback
  default makes stderr non-deterministic, breaking
  `CliRunner.invoke().output` assertions.
- `add_completion=False` — disables the auto-injected
  `--install-completion` flag. Shell completion will land later
  once the surface stabilises; until then, the flag would just
  bloat `--help` output.

## Consequences

**Pro:**

- Subcommand modules are short and declarative. Each leaf
  command is a function with type-annotated parameters; Typer
  derives the option flags, help text, and parsing.
- `typer.testing.CliRunner` cuts the per-test overhead
  considerably — subcommand tests don't have to thread
  `SystemExit` handling.
- Help output is rich and uniform across subcommands (Typer +
  click handle this for us).
- The `register(app)` pattern keeps the dependency direction
  obvious: subcommand modules pull from `cairn.config` /
  `cairn.conventions`; the root module registers them and
  knows nothing about their internals.

**Con:**

- Two new deps (`typer`, `shellingham`) plus a transitive bump on
  `click`. Versioned conservatively (`typer>=0.12,<1`).
- One small Typer pattern wart: `Path` arguments with a default
  of `None` need `Path | None` typing plus
  `typer.Argument(None, ...)`. Not unreadable but argparse
  wouldn't have needed it.
- Tests that monkey-patch `cairn.config.CURRENT_SCHEMA_VERSION`
  must also patch the import binding in `cairn.cli._config` —
  argparse code never had this concern because it never
  re-exported the constant.

## Status

Accepted, shipped at 0.14.0.
