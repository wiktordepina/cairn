# cairn

[![CI](https://github.com/wiktordepina/cairn/actions/workflows/ci.yml/badge.svg)](https://github.com/wiktordepina/cairn/actions/workflows/ci.yml)
[![Docs](https://github.com/wiktordepina/cairn/actions/workflows/docs.yml/badge.svg)](https://wiktordepina.github.io/cairn/)

A personal LLM harness built around a companion at its centre.

**Documentation:** <https://wiktordepina.github.io/cairn/>

*Cairn* (Gaelic *carn*; Welsh *carn*) — a pile of stones stacked by travellers
to mark a path, a summit, or a place worth remembering. The companion
accumulates stone by stone, session by session, through sustained use. Memory
is the cairn.

## What it is

A terminal application with a **primary companion** — an LLM with stable
identity, persistent memory, and the ability to delegate to other models as
tools. Around it:

- **Companion sessions** — long-lived, personal, memory-accumulating
- **Persona sessions** — alternative identities with isolated or no memory
- **Ephemeral sessions** — stateless, direct model access

The companion can consult specialist models (reasoning, coding) via delegation
tools. The user always sees the companion's voice.

## Architecture

Key architectural commitments:

- **Companion-first, tool-aware** — a relationship, not a task queue
- **Memory-first** — three-tier memory (observations, reflections, curated MEMORY.md)
- **Model-agnostic** — provider change = config change
- **Identity survives model swaps** — soul document + memory + context persist
- **Transparency over magic** — tool calls, costs, and memory writes are observable

## Tech stack

Today's shipped dependencies (what `uv sync` installs and the code
actually imports):

| Concern | Choice |
|---|---|
| Language | Python 3.13+ |
| Packaging | `uv` |
| Validation | Pydantic v2 |
| HTTP | httpx (via the provider SDKs and `web_fetch`) |
| Database | SQLite (aiosqlite, WAL mode) |
| Config | TOML (tomllib, stdlib) |
| Secrets | OS keychain (keyring) |
| Paths | platformdirs |
| Provider SDKs | `anthropic`, `openai` (also serves OpenRouter) |
| Tokenisation | `tiktoken` |
| File watching | `watchfiles` |
| TLS trust | `truststore` (OS trust store via `setup_ssl()`) |

Planned (not yet shipped): Textual UI, `structlog` observability,
MCP client (`mcp` SDK), `sqlite-vec` + `fastembed` for V3 vector
search. See [`docs/architecture.md`](docs/architecture.md) for the
full roadmap.

## Status

Cairn is in active assembly. The orchestrator, providers, persistence,
config, and tool system (including the runner and `DelegationTool`)
are all built and tested end-to-end, but there is **no CLI entry
point yet** — the `cairn = "cairn.cli:main"` script in `pyproject.toml`
is a placeholder for work that hasn't landed. You can drive the
library programmatically today; the user-facing terminal app arrives
with the CLI + UI bricks.

## Getting started

```bash
# Clone the repository
git clone https://github.com/wiktordepina/cairn.git
cd cairn

# Install dependencies (and run the test suite)
uv sync
uv run pytest
```

`uv run cairn` will error out until the CLI brick lands.

## Configuration

Cairn uses schema-versioned TOML configuration, layered:

1. **User** — `$XDG_CONFIG_HOME/cairn/config.toml` (profiles, providers)
2. **Project** — `<repo>/.cairn/config.toml` (project overrides, checked in)
3. **Local** — `<repo>/.cairn/config.local.toml` (personal tweaks, gitignored)

API keys are stored in the OS keychain via `keyring`, never in config files.

See [`docs/configuration.md`](docs/configuration.md) for the full TOML reference.

## Documentation

Published site (with search, a version selector, and an auto-generated
API reference): <https://wiktordepina.github.io/cairn/>.

For the same content on GitHub — including the index of guides,
architecture pages, ADRs, and the API reference — see
[`docs/README.md`](docs/README.md).

## Roadmap

- **V1** — Companion + tier-1 memory + delegation + observability
- **V2** — Reflection pipeline, knowledge graph, MCP, skills, subagents
- **V3** — Hybrid vector search, advanced features
- **V4+** — Multi-agent orchestration, plugin architecture

## Licence

TBD
