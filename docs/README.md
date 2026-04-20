# Cairn documentation

User-facing documentation for cairn. Intended audience: people using,
configuring, or packaging cairn. Not internal implementation detail — for
that, read the code.

## Contents

### Reference

- **[Architecture](architecture.md)** — the overall shape of cairn, how the
  bricks fit together, and the data flow of a turn.
- **[Configuration](configuration.md)** — every TOML key, every secret
  reference scheme, profile discovery and merge order.
- **[Providers](providers.md)** — per-provider notes for Anthropic,
  OpenAI (including OpenAI-compatible local servers), and OpenRouter.
- **[Persistence](persistence.md)** — per-profile data layout, SQLite
  specifics, backup story.
- **[Orchestrator](orchestrator.md)** — the turn loop: state machine,
  middleware seams, cancellation, crash recovery.

### Decisions

- **[Architecture Decision Records](decisions/)** — the *why* behind
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
