# Memory

Cairn's memory layer is the substrate of mutual investment: facts,
preferences, decisions, and relationships surfaced across sessions so
the companion actually gets better at working with you. This page
covers what tier 1 ships in V1, how extraction and retrieval are
wired, and where the on-disk artefacts live.

!!! note "V1 scope — tier 1 only"
    V1 ships the tier-1 observation layer: post-turn extraction into
    SQLite + JSONL, composite-scored retrieval, and read-side loading
    of MEMORY.md / soul_document.md / user_context.md. The V2
    reflection pipeline and curation UI are not part of this release.

## Layers

Three tiers, per the architecture doc:

| Tier | What | V1 status |
|---|---|---|
| 1 — Observations | Raw extractions written automatically post-turn | **Shipped** |
| 2 — Reflections | Synthesised insights from unreflected observations | Deferred to V2 |
| 3 — MEMORY.md | Curated, high-confidence facts | Read-side shipped; curation UI in V2 |

## How extraction works

After a companion turn completes, the orchestrator emits
`ObservationExtractionRequested` and calls
`ExtractionQueue.submit(session_id, since_idx, turn_id)`.
`ObservationExtractionQueue` picks up the job asynchronously — the
turn loop never waits.

The queue applies three gates before paying for an extraction:

1. **Length gate.** If the latest turn's combined user + assistant
   text is below `MemoryConfig.min_extraction_chars` (default 200), the
   job is skipped. Avoids burning utility-model calls on
   "thanks"/"ok"/"yes" turns.
2. **Persona opt-out.** Persona sessions extract by default; set
   `MemoryConfig.extract_from_personas = false` to turn off for an
   intentionally stateless persona.
3. **Tool-only turns.** Turns where the assistant produced no prose
   but did invoke tools still yield extractions by default. Disable
   with `MemoryConfig.extract_from_tool_only_turns = false`.

A job that clears the gates is dispatched under a per-memory-space
`asyncio.Lock` — two jobs for the same space serialise, but different
spaces run in parallel. The queue is bounded: overflow drops the
oldest queued job and logs a WARNING.

### The extractor call

The `Extractor` loads the latest turn plus
`MemoryConfig.extraction_context_turns` (default 3) preceding turns,
renders them with `[context]` / `[LATEST]` markers, and streams the
`ModelRole.EXTRACTION` model with the prompt at
`cairn/memory/_prompts/extract.md`. Response shape:

```json
{
  "observations": [
    {
      "content": "User prefers British English spelling.",
      "entry_type": "preference",
      "importance": 7
    }
  ]
}
```

Each observation is stored via `MemoryRepo.store()` (which runs
dedup-on-store) and appended to the JSONL log. Usage is recorded with
`operation=extraction` so cost aggregations work.

### Model selection

`ModelRole.EXTRACTION` resolves via the `ModelRegistry`. If no model
carries that role, the extractor falls back to `ModelRole.UTILITY`, so
profiles that haven't opted in to a dedicated extractor keep working
without change.

For local-model users, `ExtractionResponse.model_json_schema()` gives
a JSON schema you can wire into llama.cpp (GBNF), vLLM
(guided-decoding), or LM Studio (JSON schema) to keep a small local
model's output parseable:

```python
python -c "from cairn.memory import ExtractionResponse; import json; \
  print(json.dumps(ExtractionResponse.model_json_schema(), indent=2))"
```

Point your profile's provider `base_url` at the local server and set
the extraction model there. Typical setups run Qwen 2.5 7B Instruct
(Q4_K_M) or Llama 3.1 8B Instruct, both ~5 GB VRAM.

### Cost cap

`MemoryConfig.max_extraction_cost_usd` (default `$0.01`) is a per-call
cap. When usage events cross the threshold mid-stream, extraction
truncates and records what parsed so far. With Anthropic Haiku 4.5
prices, a typical extraction lands around $0.005–0.006, so the default
cap is about 2× typical cost.

## How retrieval works

Before each companion turn, the orchestrator's context manager calls
`MemoryService.retrieve(space, query, k)`. V1's service:

1. Pulls `3*k` candidates from `MemoryRepo.search()` (FTS5 BM25).
2. Rescores each with the arch-doc composite:

   ```text
   score = 0.3 · recency + 0.3 · importance + 0.4 · relevance
   ```

   - `recency = exp(-ln(2) · age_days / half_life)` — half-life 90
     days for semantic entries, 3 days for episodic (events and tasks
     decay fast so the retrieval layer naturally forgets them).
   - `importance = (importance - 1) / 9` — normalises 1..10 to 0..1.
   - `relevance = 1 / (1 + bm25)` — FTS5 BM25 is lower=better, so
     inversion pulls stronger matches toward 1.
3. Sorts by composite score, truncates to top-k.
4. Truncates each entry's content to
   `MemoryConfig.retrieval_content_truncate` (default 200 chars) so
   the system prompt payload stays bounded.

`composite_score()` is a pure function; golden-value tests catch
formula drift.

## System prompt assembly

`StandardContextManager` wraps each source in a tagged section:

```xml
<identity>
  <!-- soul_document.md -->
</identity>

<user_context>
  <!-- user_context.md -->
</user_context>

<memory_index>
  <!-- MEMORY.md verbatim -->
</memory_index>

<retrieved_memories>
  - [preference] User prefers British English (importance 7)
  - [relationship] Cody — 4yo brindle whippet (importance 10)
</retrieved_memories>

<persona_system_prompt>
  <!-- per-persona prompt, if any -->
</persona_system_prompt>
```

Empty sources are omitted entirely — no stubby tags. The persona
prompt is last so per-persona guidance has last-word effect. Docs are
read lazily and cached for the manager's lifetime; call
`ProfileDocLoader.invalidate()` to force a re-read (hook for
`/reload` once the UI brick lands).

## On-disk layout

```
$XDG_DATA_HOME/cairn/<profile>/
  cairn.db                              <- SQLite (memory_entries + FTS5)
  memory/
    observations/
      2026-04-22.jsonl                  <- one observation per line
      2026-04-23.jsonl

$XDG_CONFIG_HOME/cairn/<profile>/
  soul_document.md                      <- identity
  user_context.md                       <- free-form user context
  MEMORY.md                             <- curated index (short)
  memories/
    stack-and-tools.md                  <- one curated memory per file
    keto-diet.md
    ...
```

### MEMORY.md as an index

Unlike the monolithic MEMORY.md described in the architecture doc, V1
treats MEMORY.md as a **short index** of one-line entries, each linking
to a per-memory body under `memories/`. Format (matches the
convention used by `mait-code`'s auto-memory system):

```markdown
# Memory

- [Stack and tools](memories/stack-and-tools.md) — Python, Go, Terraform
- [Ketogenic diet preference](memories/keto-diet.md) — <5g sugar per 100g
```

Per-memory files carry frontmatter:

```markdown
---
name: Stack and tools
description: Primary languages, infra, OS
type: user
---

Python, Go, shell scripting. AWS, Docker, Terraform, Ansible...
```

In V1 only the MEMORY.md index is loaded into every companion prompt
(cheap, ~1–2 k tokens regardless of how many curated memories exist).
Individual `memories/<slug>.md` files are not read by the runtime —
they'll surface via retrieval once the V2 curation UI exists. Until
then, users hand-edit the index and the body files.

### JSONL observation log

Every observation extracted is appended to
`<profile>/memory/observations/YYYY-MM-DD.jsonl` as a single JSON
line. Files roll at UTC midnight. The V3 multi-machine-sync brick
will add a JSONL → SQLite rebuild tool; V1 writes both but doesn't
ship rebuild. No fsync per line — SQLite is the primary durability
store, so losing the tail on a crash is acceptable.

## Invariants

1. Every `memory_entries` row has a non-null `memory_space`.
2. All retrieval queries include `WHERE memory_space = ?`.
3. Observation extraction is never kicked off for a session with
   `memory_space IS NULL`.
4. Deleting a session does **not** delete its memory entries — they
   outlive the conversation. A cascade would be tempting and wrong:
   memory is the point.

## Configuration

All tunables live under `ProfileConfig.memory` (see the
[`MemoryConfig`](reference/config.md) reference):

| Field | Default | Purpose |
|---|---|---|
| `extraction_context_turns` | `3` | Prior turns included as disambiguation context |
| `min_extraction_chars` | `200` | Length gate — skip sub-threshold turns |
| `extract_from_personas` | `true` | Extract from persona sessions |
| `extract_from_tool_only_turns` | `true` | Extract from tool-only turns |
| `max_extraction_cost_usd` | `0.01` | Per-call cost cap |
| `max_pending_extractions` | `100` | Queue size before drop-oldest |
| `retrieval_k` | `8` | Default top-k per retrieval |
| `retrieval_content_truncate` | `200` | Per-entry char cap in system prompt |
| `observation_log_fsync` | `false` | Fsync each JSONL append |

## Troubleshooting

**"Why isn't the companion remembering X?"**

- Did the turn that mentioned X clear the length gate? Very short
  turns skip extraction.
- Is the session a persona with `extract_from_personas = false`?
- Did the extraction call hit the cost cap? Check the `model_usage`
  table with `operation='extraction'`.
- Did MEMORY.md grow past the 20 KB defensive cap? Check logs for
  `profile doc … exceeds cap` warnings.
- Did the extractor parse the response? Malformed JSON drops the
  batch; look for `failed to parse response` in the cairn log.

**"Retrieval returns nothing even though MemoryRepo has data."**

- Is the retrieval space the right one? Companion sessions use
  `companion`; persona sessions use `persona:<name>`.
- Does the query contain any significant (non-stopword) terms? All
  stopwords → empty query → empty result.
- Try `await repo.recent(space)` in a REPL to confirm rows exist
  under that space.

**"Observations extracted but I don't see them in the next turn."**

- Retrieval pulls only the top-k by composite score. A very old or
  low-importance entry can lose to fresher candidates. `/memory` will
  expose the full list once the UI ships.

## API reference

- [`cairn.memory`](reference/memory.md)
- [`cairn.persistence`](reference/persistence.md) (see `MemoryRepo`,
  `MemoryHit`, `memory_dir_for_profile`)
- [`cairn.config`](reference/config.md) (see `MemoryConfig`,
  `ModelRole.EXTRACTION`)
