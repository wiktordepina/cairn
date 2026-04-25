# Cairn documentation

User-facing documentation for cairn. Intended audience: people using,
configuring, or packaging cairn. Not internal implementation detail — for
that, read the code.

This page is the landing for both the GitHub folder view and the published
site at <https://wiktordepina.github.io/cairn/>. The site adds search, a
version selector, and an API reference generated from docstrings; the
content is otherwise the same.

## Contents

### Guide

- **[Configuration](configuration.md)** — every TOML key, every secret
  reference scheme, profile discovery and merge order.
- **[CLI](cli.md)** — `cairn config`, `cairn trust`, first-run setup,
  exit codes.
- **[Providers](providers.md)** — per-provider notes for Anthropic,
  OpenAI (including OpenAI-compatible local servers), and OpenRouter.
- **[Memory](memory.md)** — tier-1 extraction, retrieval, MEMORY.md
  loading, and the observation log.
- **[Conventions](conventions.md)** — convention-file discovery, merge
  ordering (user → ancestor → nested), and what's surfaced to the model.
- **[Compaction](compaction.md)** — turn-block truncation, advisory
  context budget, and what's deferred to V2.
- **[UI](ui.md)** — the Textual interface: keybindings, approval modal,
  trust prompt, and the single-loop observer.
- **[Observability](observability.md)** — `setup_logging()` and
  `setup_ssl()` bootstraps; resolution order; when to call.

### Architecture

- **[Overview](architecture.md)** — the overall shape of cairn, how the
  bricks fit together, and the data flow of a turn.
- **[Orchestrator](orchestrator.md)** — the turn loop: state machine,
  middleware seams, cancellation, crash recovery.
- **[Persistence](persistence.md)** — per-profile data layout, SQLite
  specifics, backup story.
- **[Tools](tools.md)** — tool system, built-in catalogue (`file_read`,
  `file_write`, `grep`, `web_fetch`), risk tiers, workspace sandbox,
  SSRF defence.

### Reference

- **[API reference](reference/index.md)** — auto-generated from each
  module's `__all__`. Covers `compaction`, `config`, `conventions`,
  `domain`, `logging`, `memory`, `orchestrator`, `persistence`,
  `providers`, `ssl`, `tools`, and `ui`.

The reference pages are best read on the published site, where
`mkdocstrings` resolves the `:::` directives into rendered docstrings
with parameter and return tables. On GitHub the same files show as
plain Markdown — useful for finding which symbols exist, less useful
for reading the docstrings themselves. See
[ADR 0020](decisions/0020-auto-generated-api-reference.md) for the
generation contract.

### Decisions

- **[Architecture Decision Records](decisions/README.md)** — the *why* behind
  non-obvious decisions. Numbered, dated, never rewritten — superseded
  when reversed. See `decisions/README.md` for the format.

## Conventions

- **British English** throughout.
- **Examples favour clarity over coverage.** Where a config key has many
  valid shapes, the example shows the one you'll want 90% of the time;
  the reference table covers the rest.
- **`<angle-brackets>` are placeholders.** Replace with your values.

## How these docs are maintained

Documentation is a first-class citizen in cairn. Every feature PR updates
the relevant user docs; non-obvious decisions land with an ADR in the same
PR as the code. The `CHANGELOG.md` records *what* changed; these docs
record *how it works* and ADRs record *why it's shaped that way*.

If you spot an inconsistency between the docs and the code, the code is
the truth and the docs are a bug — please open an issue.

The site is built with [MkDocs](https://www.mkdocs.org/) and the
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) theme.
Docstring extraction uses [`mkdocstrings`](https://mkdocstrings.github.io/)
with the Python handler; docstrings follow **Google style**. Versioned
publication uses [`mike`](https://github.com/jimporter/mike). See
[ADR 0018](decisions/0018-docs-site-tooling.md) for the rationale.

The reference pages under `reference/` are produced by
`docs/gen_ref_pages.py` from each module's `__all__` — see
[ADR 0020](decisions/0020-auto-generated-api-reference.md). After
editing `__all__`, regenerate:

```bash
uv run python docs/gen_ref_pages.py
```

CI runs the same script with `--check` to catch drift.

### Building locally

```bash
uv sync --group docs
uv run mkdocs serve   # live-reload at http://127.0.0.1:8000
```

A strict build (`uv run mkdocs build --strict`) runs on every PR and
fails on broken references, unresolved `:::` paths, and nav orphans.
