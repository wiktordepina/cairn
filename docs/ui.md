# Interactive UI

Cairn's interactive interface is a
[Textual](https://textual.textualize.io/) app: a chat transcript,
a command bar, inline tool-call rows, an activity indicator, a
cost meter, and a modal approval dialog for tier-3+ tool calls.
Start it with `cairn --profile <name>`; the bootstrap assembles
the full collaborator graph (config → persistence → providers →
memory → tools → orchestrator) on a single asyncio event loop
before mounting the app.

!!! note "V1 scope — tranche 1"
    Tranche 1 ships a single-session chat screen with streaming
    Markdown, tool-call lifecycle rows, tier-3+ approval modal,
    slash-command dispatch + completion popover, a four-state
    activity indicator, and a live cost meter backed by
    `UsageRepo`. Session switching, delegation inline cards, and
    the memory-write toast land in tranche 2. The crash-recovery
    banner, `/context` rendering, and the real prompt-trust gate
    land in tranche 3.

## Layout

A session screen composes five widgets top-to-bottom:

1. **`SessionHeader`** — session-type badge (companion / persona /
   ephemeral, colour-keyed per
   [`UIConfig.session_type_colours`](#ui-configuration)), session
   title, and the docked `ActivityIndicator`.
2. **`ChatLog`** — scrolling container of `MessageView`,
   `ToolRow`, and `Banner` widgets. Auto-scrolls to the tail when
   the user is already near the bottom; respects manual scroll-up
   within a 3-row slack.
3. **`CompletionMenu`** — hidden by default; opens into a
   selectable list of slash-command matches when the command bar
   starts with `/`.
4. **`CommandBar`** — single-line input. Plain text submits a
   user turn; `/command` dispatches through the registry.
5. **`CostMeter`** — running session spend, precision controlled
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

## Tool-call rows

Each planned tool call mounts a `ToolRow` in the chat log with a
state machine:

- **planned** — row appears; args rendered inline.
- **approved / rejected** — coloured badge updates.
- **running** — animated braille spinner appears to the left of
  the tool name.
- **completed** — spinner replaced by a status glyph (`✓` success,
  `✗` failure, `⏱` timeout), duration rendered in ms.

While any tool is running, the header `ActivityIndicator` flips
to `tool:<name>`. Between streaming and tool calls it settles to
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

The command bar accepts a small set of tranche-1 commands:

| Command | Summary |
|---|---|
| `/help` | Print the command catalogue |
| `/cost` | Show current session cost |
| `/tools` | List tools available in this session |
| `/new` | Placeholder — session-type picker lands in tranche 2 |
| `/ephemeral <model>` | Placeholder — ephemeral spawn lands in tranche 2 |
| `/quit` | Exit the app |

### Completion popover

Type `/` to open the `CompletionMenu` above the command bar. It
filters as you type (prefix match, alphabetical). Key bindings
while the menu is open:

- <kbd>↑</kbd> / <kbd>↓</kbd> — move the highlight.
- <kbd>Tab</kbd> — complete the highlighted command into the
  input (with a trailing space so the menu closes — a space
  implies arguments are next).
- <kbd>Esc</kbd> — dismiss the menu without clearing the input.
- <kbd>Enter</kbd> — submits the current input value (always).

Custom slash commands registered via `CommandRegistry.register`
appear in the popover automatically — the menu reads the registry
at keystroke time, not at mount.

## Activity indicator

Docked to the right of the header. Four states:

- **idle** — empty, muted colour.
- **thinking** — the model is composing (between request and
  first delta).
- **streaming** — text deltas are flowing.
- **tool:&lt;name&gt;** — a tool call is executing.

States are driven by observer callbacks; the spinner frames at
100 ms intervals while non-idle.

## UI configuration

All UI knobs live on `ProfileConfig.ui`:

```toml
[profiles.companion.ui]
theme = "auto"                           # auto | light | dark
show_cost_in_header = true
max_chat_log_messages = 500
cost_display_precision = 6               # 0..10 decimal places

[profiles.companion.ui.session_type_colours]
companion = "#2aa198"
persona   = "#cb4b16"
ephemeral = "#586e75"
```

All fields are optional; the defaults match the palette above.
Session-type colours must be 7-character `#RRGGBB` hex strings —
invalid values fail validation at config load.

## Crash recovery

If the orchestrator was killed mid-turn (SIGKILL, process crash,
host restart), the `turns` table will contain non-terminal rows
on next boot. `Orchestrator.resume_aborted_turns()` marks them
aborted with `reason='process_crash'`. The UI doesn't surface
this today — a banner announcing "recovered N aborted turns"
lands in tranche 3.

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
