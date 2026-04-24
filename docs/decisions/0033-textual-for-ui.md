# 0033 — Textual for the interactive UI

**Date:** 2026-04-24
**Status:** accepted

## Context

Cairn's primary interface is a long-running, bidirectional
conversation with a model: streaming text deltas, tool-call
lifecycle rows, inline approval prompts, background budget and
cost signals, slash commands. The UI brick needs to:

- Render streaming text token-by-token without flicker.
- Present modal approval dialogs mid-turn without tearing down
  the transcript behind them.
- Run on the same asyncio loop as the orchestrator, the aiosqlite
  Database handle, and the extraction worker (see ADR 0034).
- Work in plain terminals over SSH — no GUI toolkit, no browser
  dependency.
- Ship as a pip-installable Python app with no separate frontend
  toolchain.

Three shapes were considered:

1. **Rich-only** — write to the terminal via
   [Rich](https://github.com/Textualize/rich)'s `Live` renderer,
   manage screen state by hand. This is how `mait-code`'s earlier
   TUI was built. Works for one-shot output; scales poorly once
   you need input focus, modal dialogs, and scrollable regions
   with independent lifetimes.
2. **prompt-toolkit** — battle-tested (IPython, ptpython, a long
   list of REPLs). Excellent at line editing and autocomplete.
   Layout and styling primitives are more primitive than Textual's,
   and the modal/overlay story is hand-rolled.
3. **Textual** — layout + CSS + widget + message bus, built on
   Rich underneath. Purpose-built for app-like TUIs. Runs on an
   asyncio event loop by design; the pilot harness (`App.run_test()`
   + `Pilot`) makes widget tests practical.
4. **Web UI (FastAPI + htmx or similar)** — genuinely useful
   eventually (V2+ via `textual-serve` or bespoke), but V1 is a
   personal tool run locally. A browser dependency is scope creep
   for the MVP.

## Decision

Build the V1 UI on Textual. Specifically:

- `App`, `Screen`, `Widget`, `Static`, `Input`, `Label`,
  `ModalScreen`, `OptionList` as the building blocks.
- CSS for layout and theming (`DEFAULT_CSS` per widget; no
  external `.tcss` for V1 — keeps everything colocated with the
  widget source).
- `App.run_test()` + `Pilot` as the test harness. Every widget
  and every observer callback in tranche 1 has pilot coverage.
- `ModalScreen` for tool-call approval (tier 3+), cleanly
  overlaying the session transcript without tearing it down.

Version pin is a lower bound (`textual>=...`) rather than an exact
match; Textual follows semver and breakage has been rare in
practice. The pin is revisited on each tranche.

## Consequences

**Easier:**

- Streaming renders are one `Static.update(text)` call; Textual's
  renderer handles diffing and terminal output. `MessageView`
  streams plain text mid-flight and swaps to a Markdown renderable
  on seal (see follow-up in `feat/ui`).
- Modal approval is a `push_screen_wait(ApprovalModal)` call;
  the orchestrator awaits the user's decision on the same loop
  it's running on. No thread hopping, no `call_from_thread`
  (ADR 0034).
- Tests are cheap and honest: the pilot drives real widgets on a
  real-but-synthetic event loop. Widget-state bugs show up in
  tests, not live.

**Harder:**

- Textual's abstractions leak in a few places (binding priority,
  key consumption order, focus semantics). Each one costs a
  reading of the source the first time; subsequent widgets
  inherit the lesson.
- Rich-only output for simple scripted runs is no longer free —
  the UI brick assumes an interactive terminal. Non-interactive
  output paths (future headless `cairn run` or CI hooks) will
  need a separate thin layer.

**Ongoing cost:**

- Textual's API is still settling — the team ships fast, and
  minor-version deltas occasionally rename reactive attributes
  or message classes. The pilot test suite catches most of this;
  the lockfile pins exact versions for reproducible CI.
- Any V2 web-UI ambition will share the domain / orchestrator /
  observer contracts but *not* the widget tree. That's fine —
  the UI brick is a thin adapter over the event stream, so a
  parallel implementation is a rewrite of widgets, not of
  logic.
