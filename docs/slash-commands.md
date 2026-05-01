# Slash commands

The command bar in the interactive UI accepts the following commands.
This page is the canonical reference; feature pages link back here
rather than re-documenting individual commands.

| Command | Summary |
|---|---|
| `/help` | Print the command catalogue |
| `/cost` | Show session cost + today / last 3 days / month-to-date totals (see below) |
| `/context` | Show context-budget usage (see below) |
| `/tools` | List tools available in this session |
| `/persona` | Show the active session's persona |
| `/profile` | Show active profile name + key fields |
| `/model` | Pick a configured model for this session (see below) |
| `/conventions` | List discovered convention files and the project's trust state |
| `/reload` | Reload config + conventions + profile docs (see below) |
| `/recall <query>` | Search session memory; show top-k hits in a modal (see below) |
| `/remember <text>` | Save a fact to session memory (see below) |
| `/clear` | Archive the current session and start fresh |
| `/archive` | Archive the current session and quit (see below) |
| `/ephemeral <model>` | Placeholder — ephemeral spawn lands post-0.10.0 |
| `/quit` | Exit the app |

> **Future:** `/compact` lands with LLM-driven compaction at
> 0.20.0; `/forget` is V2 and ships with the tier-3 curation UI.

## `/cost`

Renders the session's running cost alongside a four-row report:

```
session cost: $0.04

                 current profile  all profiles
today                      $0.41         $0.41
last 3 days                $1.28         $1.28
month-to-date              $5.67         $5.79
```

- **today** — since 00:00 local time on the current calendar day.
- **last 3 days** — rolling 72h ending now.
- **month-to-date** — since 00:00 local on the 1st of the current
  calendar month.

Local-time boundaries come from `[locale.timezone]` (see
configuration); the orchestrator stamps `profile` on every
`model_usage` row at record time so per-profile filtering is
cheap. Two-decimal precision throughout — see ADR 0047 for the
schema rationale.

## `/model`

Opens a picker over every model declared in `[models.*]`, with
the session's current model marked. Each row renders as
`<display_name> | <id>` (the pipe separator avoids clashing with
display names that already contain parentheses, like
`"DeepSeek V3.1 (OpenRouter)"`). Selecting the current model is
a no-op. Selecting a *different* model on a fresh session
applies the swap immediately; on an in-progress session, a
follow-up modal asks how to handle history:

- **keep history** — same session row, model column updated,
  transcript preserved. Next turn re-streams under the new model.
  Prompt cache invalidates (cache markers are model-scoped); the
  auto-compactor will trim aggressively if the new model has a
  smaller context window.
- **start fresh** — archive the current session, open a new one
  of the same type / persona on the picked model.
- **abort** — no change.

`/reload` reverts `session.model` to whatever the config now
resolves to — config is the single source of truth, so a runtime
swap disappears on the next reload. See ADR 0045.

## `/clear`

Archives the current session and opens a fresh one of the same
type and persona. Refused mid-turn — finish the current turn
first.

## `/archive`

Archives the current session. V1 has no session picker yet, so
post-archive there is nowhere to go — the command opens a
confirm modal warning that archive will close the app. On
accept: archive + quit. On Esc / cancel: no-op. ADR 0045
records the rationale and the V2 follow-up to drop the quit
once a picker exists.

## `/context`

Shows the current session's context-budget footprint as an inline
muted banner:

```
context: 17,000 / 200,000 tokens (8% used) — model=Claude Opus 4.7
  cached: 10,000 (read) + 2,000 (write)
  fresh:  5,000
  output: 800 (this turn)
  (segment breakdown unavailable in V1)
```

Reads the most-recent `PRIMARY_TURN` usage row from `UsageRepo`
and the model's `context_window` from `ModelConfig`. The cache
columns reflect real provider usage as of 0.13.0 — see
[Prompt caching](prompt-caching.md) for the per-provider shape
of `cached: read` and `cached: write`. The per-segment breakdown
(`identity`, `user_context`, `project_conventions`,
`memory_index`, `retrieved_memories`) needs a `ContextReport`
from the context manager and lands with the stacked-bar
follow-up. If the session has no usage recorded yet (fresh
session, first turn still running), the banner shows the budget
only with a "no primary-turn usage recorded yet" note.

## `/reload`

Re-reads the merged config, convention files, and profile docs
from disk and applies the changes surgically — without
restarting the session, the extraction worker, or the Textual
UI. The success banner names the categories that were re-snapped
(*"Reloaded: 2 config layers, 1 convention file."*); a failed
reload (validation error) renders a warning banner with the
error message and leaves the previously-good config in place.

When invoked mid-turn, `/reload` queues until the current turn
completes — the in-flight provider call finishes against the
bound registries, and the swap takes effect on the next turn.
The user sees a *"reload queued — applies after current turn"*
banner immediately, then the result banner once the deferred
reload runs.

What `/reload` does and doesn't swap is documented in
[ADR 0042](decisions/0042-surgical-reload-boundary.md). The
short version: provider registry, model registry, and secret
resolver are rebuilt; the orchestrator instance, active session,
persistence, and extraction queue are preserved.

## `/recall`

Searches the active session's memory for entries matching the
query. Results render in a modal overlay listing the top hits
with composite score, type, importance, and full content body —
nothing about the recall is appended to the conversation, so the
LLM does not see the results.

```
recall: "coffee"  (3 hit(s))

[0.84]  preference  imp=8  #42
Wiktor takes coffee with no sugar, oat milk, single shot.

[0.71]  fact  imp=5  #18
Coffee shop near the office is closed on Mondays.

[0.58]  fact  imp=4  #7
Espresso machine at home is a Rancilio Silvia.
```

The composite score is the same value `MemoryService.retrieve()`
uses internally (recency + importance + BM25 relevance). Score
visibility is intentional — same posture as `/cost` exposing
dollar figures rather than hiding them. Empty results render
*"no memories matched that query."*.

The modal scopes to the active session's `memory_space`; if the
current session has no memory space (ephemeral, or a persona
without one), the slash command shows a muted banner instead.
Esc closes.

> **Symmetric tool:** the `recall` built-in tool gives the LLM
> the same capability through a tool call. Slash output is
> human-only; the tool's JSON output becomes part of the next
> provider request. See [Tools](reference/tools.md).

## `/remember`

Saves a `fact` memory to the active session's `memory_space`,
bypassing the post-turn observation queue. Importance defaults to
**7** (one notch above the extractor default of 5 — explicit user
intent is a stronger signal than auto-extraction); override per
profile with `[memory] explicit_remember_importance = N` (1..10).

Confirmed via toast:

- *"remembered"* — fresh insert.
- *"already remembered (entry #N)"* — dedup hit, where `N` is the
  existing entry's id. The `MemoryRepo` runs the same
  similarity-threshold dedup as the post-turn extractor; saying
  the same thing twice refreshes the existing row's `updated_at`
  rather than creating a duplicate.

V1 has no inline flags for entry type or importance — every
`/remember` writes a `fact` with the configured importance. If
you want a memory gone, hand-edit `<data_dir>/cairn.db`; the
tier-3 curation UI lands in V2.

## Completion popover

Type `/` to open the `CompletionMenu` above the command bar. It
filters as you type (prefix match, alphabetical). Key bindings
while the menu is open:

- <kbd>↑</kbd> / <kbd>↓</kbd> — move the highlight.
- <kbd>Tab</kbd> — complete the highlighted command into the
  input (with a trailing space so the menu closes — a space
  implies arguments are next). Use this when you want to type
  arguments before submitting.
- <kbd>Enter</kbd> — runs the highlighted command directly. The
  typed prefix is replaced with the highlight's full name and
  submitted in one keystroke. If the menu is closed (or has no
  highlight), Enter submits the input value as typed. See
  [ADR 0037](decisions/0037-enter-executes-highlighted-command.md).
- <kbd>Esc</kbd> — dismiss the menu without clearing the input.

Custom slash commands registered via `CommandRegistry.register`
appear in the popover automatically — the menu reads the registry
at keystroke time, not at mount.
