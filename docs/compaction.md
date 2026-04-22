# Compaction

Every long-lived conversation eventually outgrows the model's
context window. Cairn's compaction brick keeps the provider
request under budget by dropping the oldest turns from the
conversation history before each provider call.

!!! note "V1 scope — truncation only"
    V1 compaction is truncation with a preserve-floor. LLM-driven
    summarisation (turn the oldest span into a paragraph) is a V2
    feature; the foundation below is the shape V2 will slot into
    as a second strategy behind the same `MessagePreparer`
    interface.

## What it does

`TruncatingCompactor` is a `MessagePreparer` that sits in the
orchestrator's prepare chain. On every turn:

1. Calculate the effective budget:
   `context_window − max_tokens − safety_margin_tokens`.
2. Count tokens for the full request via the provider's native
   tokenizer.
3. If the request fits, return it unchanged.
4. If not, group `request.messages` into **turn blocks** and
   drop them from the front one at a time, re-counting after
   each drop, until the request fits or the preserve-floor
   (`preserve_last_n_turns`) is reached.
5. If the floor is reached and the request is *still* over
   budget, emit a `BudgetOverflowAdvisory` and hand the
   decision to the configured `BudgetOverflowGateway`.

Every successful compaction emits a `HistoryCompacted` UI event
so the UI can show "history truncated — N messages dropped".

## What it doesn't do

- It doesn't touch the identity triple, MEMORY.md, convention
  files, retrieved memories, tool definitions, or the current
  user turn. Those all live in `ProviderRequest.system` and
  related protected segments (see [architecture §4.12](architecture.md)).
- It doesn't summarise. Dropped turns are gone as far as the
  model is concerned — but the memory brick has already
  extracted durable facts into `memory_entries`, so long-lived
  information survives compaction via a different path.
- It doesn't slice mid-turn. Dropping a partial turn breaks
  tool-use/tool-result pairing (see
  [ADR 0028](decisions/0028-turn-block-granularity.md)).

## Turn blocks

A "turn block" is a contiguous run of messages that starts at a
user message whose content is *not* purely tool results. A block
that contains an assistant tool call always also contains the
user message that carries the tool result, so dropping the block
is always safe.

```text
[user: "find X"]              ┐
[assistant: tool_use(grep)]   ├─ one turn block
[user: tool_result]           │
[assistant: "found it"]       ┘
[user: "now edit"]            ┐
[assistant: "done"]           ┘ another turn block
```

See [ADR 0028](decisions/0028-turn-block-granularity.md) for the
full rationale.

## The advisory budget

The **compaction budget is advisory, not hard.** If the
preserve-floor is reached and the surviving request is still
over budget, `TruncatingCompactor` doesn't silently block or
silently overflow — it consults a `BudgetOverflowGateway`:

- `"continue"` — the request goes to the provider as-is. The
  surviving messages often still fit the model's hard
  `context_window` because `safety_margin_tokens` leaves real
  headroom.
- `"terminate"` — the compactor raises `BudgetOverflowDeclined`.
  The orchestrator catches this, emits
  `TurnAborted(reason="user_declined_overflow")`, and archives
  the session.

Two stub gateways ship:

- `AutoContinueOverflowGateway` — always `"continue"`.
- `AutoTerminateOverflowGateway` — always `"terminate"`; the
  orchestrator default, so headless and CI runs fail loudly.

The interactive gateway (UI prompt with amber warning + icon,
per-session decision memory) lands with the UI brick.

Full rationale in
[ADR 0029](decisions/0029-context-budget-advisory.md).

## Configuration

`CompactionConfig` hangs off `ProfileConfig.compaction`, so every
persona gets its own settings:

```toml
[profiles.companion.compaction]
enabled = true
preserve_last_n_turns = 6
safety_margin_tokens = 2048
min_history_tokens = 1024

[profiles.tool-heavy-persona.compaction]
preserve_last_n_turns = 12   # longer floor for agentic runs
```

| Knob | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Master kill switch. `false` disables truncation entirely. |
| `preserve_last_n_turns` | `6` | Hard floor on surviving turn blocks. |
| `safety_margin_tokens` | `2048` | Held back from `context_window` on top of reserved output tokens. |
| `min_history_tokens` | `1024` | If the effective budget falls below this, log ERROR and pass the request through unchanged. |

## UI events

| Event | When |
|---|---|
| `HistoryCompacted{reason="budget"}` | Normal path — blocks dropped, request now fits. |
| `BudgetOverflowAdvisory` | Preserve-floor hit, request still over budget; gateway decision pending. |
| `HistoryCompacted{reason="preserve_floor_hit"}` | Gateway picked `"continue"`. |
| `TurnAborted(reason="user_declined_overflow")` | Gateway picked `"terminate"`. |
| `SessionArchived` | Follow-up to the terminate path — session is archived. |

## Wiring

At orchestrator construction:

```python
from cairn.compaction import TruncatingCompactor

compactor = TruncatingCompactor(
    profile.compaction,
    model_registry,
    provider_registry,
    overflow_gateway=ui_overflow_gateway,  # or a stub pre-UI
)

orchestrator = Orchestrator(
    ...,
    preparers=(compactor,),
)
```

## Related ADRs

- [ADR 0027 — Truncation before summarisation](decisions/0027-truncation-before-summarisation.md)
- [ADR 0028 — Turn-block granularity](decisions/0028-turn-block-granularity.md)
- [ADR 0029 — Context budget is advisory](decisions/0029-context-budget-advisory.md)
