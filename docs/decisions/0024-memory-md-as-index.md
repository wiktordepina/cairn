# 0024 — `MEMORY.md` as an index of `memories/<slug>.md` bodies

**Date:** 2026-04-22
**Status:** accepted
**Deviates from:** architecture doc §4.11 (which describes a
monolithic MEMORY.md).

## Context

The architecture doc describes MEMORY.md as "the highest-confidence
facts, loaded into every companion session… hard cap around 150
lines." Under that model, every curated fact lives inline in the
single file, and every turn's system prompt pays the full MEMORY.md
payload even when only a fraction of the content is relevant.

The user asked for the `mait-code` / Claude-Code convention instead:
MEMORY.md is a *short* index of links, and each curated memory
lives in its own file under `memories/`, with frontmatter. Rationale:

- **Token-cost stability.** A 100-line index stays ~1–2 k tokens no
  matter how many curated memories accumulate. A monolithic file
  grows unbounded and bleeds the system-prompt budget.
- **Consistency with the user's existing system.** Cairn will sit
  alongside mait-code in the user's workflow; matching the same
  file layout means curated memories can be migrated between
  systems without reformatting.
- **Clean V2 path.** When the reflection pipeline lands and starts
  auto-proposing curated memories, it can write a new
  `memories/<slug>.md` + append one line to MEMORY.md without
  touching a monolithic blob.

## Decision

**Curated memory in V1 has two on-disk surfaces:**

- `MEMORY.md` — a short index of `- [Title](memories/<slug>.md) —
  hook` lines. Loaded verbatim into every companion system prompt
  via `ProfileDocLoader.load_memory_index()`.
- `memories/<slug>.md` — per-memory body files carrying frontmatter:

  ```markdown
  ---
  name: Stack and tools
  description: Primary languages, infra, OS
  type: user
  ---

  Python, Go, shell scripting. AWS, Docker, Terraform...
  ```

**V1 loads MEMORY.md only.** The `memories/` folder exists on disk
and is hand-edited by the user; individual body files are NOT read
by the runtime in V1. Retrieval unification — surfacing specific
body files when relevant to the current query — is a V2 concern,
tied to the curation UI.

**The frontmatter keys (`name`, `description`, `type`) match
mait-code's convention** so users running both systems can copy
files between them.

## Consequences

- **Positive.** System prompt token cost of the curated tier is
  bounded and predictable.
- **Positive.** Body files can grow arbitrarily without hurting
  every turn's context budget.
- **Positive.** V2 can add a retrieval path that parses
  frontmatter, indexes `description` into `memory_entries`, and
  fetches specific bodies on demand — without changing the on-disk
  format.
- **Negative.** Contents of `memories/<slug>.md` files are invisible
  to the companion until V2. A user who writes a detailed body
  expecting it to be in context will be surprised. The docs page
  calls this out.
- **Negative.** Deviates from the architecture doc. Future readers
  diffing the code against §4.11 will notice — this ADR exists so
  they find the reason quickly.

## Alternatives considered

**Ship the monolithic MEMORY.md per the architecture doc.**
Predictable, matches the spec, but breaks the token-cost property
once curated memory accumulates.

**Index + load all bodies into context.** Same token blowout
problem as monolithic.

**Index only, with a `/memory show <slug>` tool that loads a body
on demand.** Attractive, but needs a tool-calling surface and a
curated-memory search index — both V2 scope. Deferred.

**Frontmatter with our own keys (not matching mait-code).** Would
fragment the user's cross-system workflow. Rejected.
