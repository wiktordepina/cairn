# cairn

A personal LLM harness built around a companion at its centre.

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

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Packaging | `uv` |
| UI | Textual |
| Validation | Pydantic v2 |
| HTTP | httpx |
| Async | anyio |
| Database | SQLite (aiosqlite, WAL mode, FTS5) |
| Logging | structlog |
| Config | TOML (tomllib) |
| Secrets | OS keychain (keyring) |
| Paths | platformdirs |

## Getting started

```bash
# Clone the repository
git clone https://github.com/wiktordepina/cairn.git
cd cairn

# Install dependencies
uv sync

# Run
uv run cairn
```

## Configuration

Cairn uses schema-versioned TOML configuration, layered:

1. **User** — `$XDG_CONFIG_HOME/cairn/config.toml` (profiles, providers)
2. **Project** — `<repo>/.cairn/config.toml` (project overrides, checked in)
3. **Local** — `<repo>/.cairn/config.local.toml` (personal tweaks, gitignored)

API keys are stored in the OS keychain via `keyring`, never in config files.

## Roadmap

- **V1** — Companion + tier-1 memory + delegation + observability
- **V2** — Reflection pipeline, knowledge graph, MCP, skills, subagents
- **V3** — Hybrid vector search, advanced features
- **V4+** — Multi-agent orchestration, plugin architecture

## Licence

TBD
