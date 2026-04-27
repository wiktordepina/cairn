# Interactive UI

Cairn's interactive interface is a
[Textual](https://textual.textualize.io/) app: a chat transcript,
a command bar, inline tool-call rows, an activity indicator, a
cost meter, and a modal approval dialog for tier-3+ tool calls.
Start it with `cairn --profile <name>`; the bootstrap assembles
the full collaborator graph (config → persistence → providers →
memory → tools → orchestrator) on a single asyncio event loop
before mounting the app.

!!! note "V1 scope"
    Ships at 0.10.0: single-session chat screen with streaming
    Markdown, tool-call lifecycle rows, tier-3+ approval modal,
    slash-command dispatch + completion popover, a four-state
    activity indicator, a live cost meter backed by `UsageRepo`,
    `/context` usage rendering, a crash-recovery banner, and the
    real trust-prompt modal for `trust_policy="prompt"` on
    project convention files. 0.11.0 adds the display-only slash
    commands (`/persona`, `/profile`, `/model`, `/conventions`)
    and one-keystroke command launching from the completion
    popover. Session switching, delegation inline cards,
    memory-write toast, and the rest of the extended slash
    catalogue land as further polish PRs.

## Layout

A session screen composes six widgets top-to-bottom:

1. **`SessionHeader`** — session-type badge (companion / persona /
   ephemeral, colour-keyed per
   [`UIConfig.session_type_colours`](#ui-configuration)), persona
   name, the active model's `display_name`, and the session
   title — separated by `·`.
2. **`ChatLog`** — scrolling container of `MessageView`,
   `ToolRow`, and `Banner` widgets. Auto-scrolls to the tail when
   the user is already near the bottom; respects manual scroll-up
   within a 3-row slack.
3. **`CompletionMenu`** — hidden by default; opens into a
   selectable list of slash-command matches when the command bar
   starts with `/`.
4. **`ActivityIndicator`** — italic single-row spinner + label
   sitting just above the command bar so the current
   thinking / streaming / tool state stays in the user's eye-line.
5. **`CommandBar`** — multi-line auto-grow input (1 row → 10 rows
   then scrolls). Plain text submits a user turn; `/command`
   dispatches through the registry. Enter submits;
   <kbd>Shift</kbd>+<kbd>Enter</kbd> /
   <kbd>Alt</kbd>+<kbd>Enter</kbd> / <kbd>Ctrl</kbd>+<kbd>J</kbd>
   insert a newline. <kbd>↑</kbd> / <kbd>↓</kbd> walk the
   per-project prompt history when the cursor is at the
   first / last line and the completion menu is closed.
6. **`CostMeter`** — running session spend, precision controlled
   by `UIConfig.cost_display_precision` (default 6 decimal
   places). Refreshed from `UsageRepo` on every `TurnComplete`.

## Message rendering

`MessageView` streams assistant output as plain text during a
turn and swaps to a rich Markdown render when the
`AssistantMessageComplete` event arrives. Mid-turn re-rendering
of partially-parsed Markdown produces visible artefacts (stray
`/`, mismatched headings, half-open code fences), so plain-text
streaming is deliberate — the final render is the authoritative
one.

User messages render immediately on submission (staged by the
screen, mounted when `UserMessagePersisted` arrives from the
observer). The observer is a single-loop synchronous fan-out
from the orchestrator — see
[ADR 0034](decisions/0034-single-loop-sync-observer.md).

### Thinking blocks

Models with reasoning support (Anthropic thinking, DeepSeek
`reasoning_content`) stream their reasoning as
`AssistantThinkingDelta` events alongside the main text stream.
Each contiguous thinking phase mounts a `ThinkingRow` widget in
the chat log: a muted, **default-collapsed** block with a
header carrying a live elapsed-time counter while streaming
("thinking… (3.2s)"), which freezes to past tense ("thought
(3.2s)") when the next non-thinking event arrives (text delta,
tool call, message complete).

Press space when a `ThinkingRow` is focused to expand or
collapse it. Default-collapsed because thinking is long and
low-signal; the header makes the *fact* that thinking happened
visible without dominating the transcript.

A "thinking → text → more thinking" sequence mounts two
separate `ThinkingRow` widgets — one per phase. Replay of
historical thinking on `/resume` is not yet implemented;
thinking content is persisted (round-trips through `MessageRepo`
in the message's `content_json`) but only renders live as the
deltas arrive.

### Delegation events

When a delegation tool spawns a child session, the parent
transcript shows two muted inline banners:

- `⤷ delegated to ephemeral session abcdef12…` — fired after
  the child session row is created in the database.
- `⤴ delegation abcdef12… returned` — fired before the child
  session is archived, so subscribers see the lifecycle close
  even if the provider stream fails mid-flight.

The full child-session transcript stays in its own session row
(reachable via the session list); these banners are just a
hint that delegation happened in the parent's turn flow.

## Tool-call rows

Each planned tool call mounts a `ToolRow` in the chat log with a
state machine:

- **planned** — row appears; args rendered inline.
- **approved / rejected** — coloured badge updates.
- **running** — animated braille spinner appears to the left of
  the tool name.
- **completed** — spinner replaced by a status glyph (`✓` success,
  `✗` failure, `⏱` timeout), duration rendered in ms.

While any tool is running, the `ActivityIndicator` flips to
`tool:<name>`. Between streaming and tool calls it settles to
`thinking`; idle after the turn completes.

## Approval modal

Tier-3 and tier-4+ tool calls trigger an `ApprovalModal` —
`push_screen_wait` on the modal blocks the orchestrator's approval
middleware on the same event loop. Keybindings: `y` approves,
`n` rejects, `escape` rejects-and-dismisses.

- **Tier 3** (e.g. `file_write`, delegation): the "Remember this
  exact call for the session" checkbox is enabled. Ticking it
  populates the `SessionAllowlist` so subsequent identical calls
  skip the modal. See
  [ADR 0015](decisions/0015-session-allowlist-exact-match.md) for
  the `(tool_name, sha256(sorted_json(args)))` match rule.
- **Tier 4+**: the checkbox is rendered but disabled, with a
  sub-label explaining why. See
  [ADR 0035](decisions/0035-remember-for-session-gating.md) for
  the reasoning behind this asymmetry.

Args render as a truncated top-level list by default. At tier-4+
they render as full pretty-printed JSON so the user can see every
value being approved.

## Slash commands

The full catalogue, per-command behaviour, and completion-popover
key bindings live on a dedicated reference page:
[Slash commands](slash-commands.md).

## Activity indicator

Sits in its own row just above the command bar. Four states:

- **idle** — empty, muted colour.
- **thinking** — the model is composing (between request and
  first delta).
- **streaming** — text deltas are flowing.
- **tool:&lt;name&gt;** — a tool call is executing.

States are driven by observer callbacks; the spinner frames at
100 ms intervals while non-idle. The header used to host this
widget; it was moved next to the input so the user can see what
the agent is doing without taking their eyes off where they
type.

## UI configuration

All UI knobs live on `ProfileConfig.ui`:

```toml
[profiles.companion.ui]
theme = "auto"                           # auto | light | dark
show_cost_in_header = true
max_chat_log_messages = 500
cost_display_precision = 6               # 0..10 decimal places
prompt_history_size = 200                # 0..10000 prompts retained per project

[profiles.companion.ui.session_type_colours]
companion = "#2aa198"
persona   = "#cb4b16"
ephemeral = "#586e75"
```

All fields are optional; the defaults match the palette above.
Session-type colours must be 7-character `#RRGGBB` hex strings —
invalid values fail validation at config load. Prompt history
persists per project at
`$XDG_DATA_HOME/cairn/<profile>/history/<project-hash>.jsonl`;
set `prompt_history_size = 0` to disable it.

## Crash recovery

If the orchestrator was killed mid-turn (SIGKILL, process crash,
host restart), the `turns` table will contain non-terminal rows
on next boot. The bootstrap calls
`Orchestrator.resume_aborted_turns()` once before mounting the
app; any non-zero count surfaces as a muted banner at the top of
the chat log:

```
↺ recovered 2 aborted turns from previous run
```

The banner is one-shot — a second screen mount in the same
process will not duplicate it. There is no interactive
recovery flow in V1 (the model's partial reply is persisted as
far as it got and the conversation simply continues).

## Convention-file trust prompt

When `trust_policy="prompt"` is set on the active profile
(`[profiles.*.convention_files]`), the UI presents a
`TrustPromptModal` on first encounter with each project's
convention files. The modal shows the project path and the list of
filenames discovered, then offers three outcomes:

- **Trust once** (<kbd>o</kbd>) — ALLOW for this process only.
- **Trust project** (<kbd>p</kbd>) — ALLOW plus persist to
  `$XDG_CONFIG_HOME/cairn/trusted_projects.toml` so future
  processes default to ALLOW without re-prompting.
- **Deny** (<kbd>n</kbd> / <kbd>Esc</kbd>) — skip loading this
  project's convention files.

<kbd>←</kbd> / <kbd>→</kbd> cycle button focus and <kbd>Enter</kbd>
activates the focused button; default focus is on **Deny** so a
stray Enter on a stranger's project never widens trust. Decisions
cache per-process, so repeated checks within the same session
never re-prompt. Projects already in the allowlist bypass the
modal entirely. See
[ADR 0036](decisions/0036-trust-prompt-three-way-choice.md) for
why we offer three outcomes rather than two and
[`docs/conventions.md`](conventions.md) for the underlying
trust-policy semantics.

## Troubleshooting

**HTTPS errors behind a corporate MITM proxy (Netskope, Zscaler).**
The bootstrap calls `cairn.ssl.setup_ssl()` which injects the OS
trust store into Python's `ssl` module via `truststore`. If this
fails silently (Debug log: `truststore injection failed`),
install `truststore` or set `SSL_CERT_FILE` manually before
launching.

**`cairn` exits immediately with no UI.** Check
`$XDG_STATE_HOME/cairn/cairn.log` (platform-specific location via
`platformdirs.user_log_path`) for the bootstrap traceback. The
file logger is set up before any widget is mounted, so early
errors are captured.

**Cost meter stuck at `$0.000000`.** The bootstrap wires the
meter's `cost_source` callback to `UsageRepo.total_cost_for_
session`. If the source raises, the callback swallows the error
silently (a cost-refresh failure must not break a turn). Check
the log for `refresh-cost` worker exceptions.

**Approval modal doesn't appear for a tier-3 tool.** Verify the
tool's decorator tier (`@tool(tier=3)`). Tier ≤ 2 with
`side_effects ∈ {none, read}` is auto-approved by the
`AutoApproveReadOnly` approver — the modal is the last step of
a chain, not the first.

## Design notes

- [ADR 0033](decisions/0033-textual-for-ui.md) — why Textual over
  Rich-only / prompt-toolkit / web.
- [ADR 0034](decisions/0034-single-loop-sync-observer.md) — the
  single-loop invariant and why the observer dispatches directly
  instead of via `call_from_thread`.
- [ADR 0035](decisions/0035-remember-for-session-gating.md) —
  why the "remember for this session" checkbox is disabled at
  tier 4+.
- [ADR 0036](decisions/0036-trust-prompt-three-way-choice.md) —
  why the trust-prompt modal offers three outcomes (trust-once
  vs trust-project vs deny) instead of a binary allow/deny.
