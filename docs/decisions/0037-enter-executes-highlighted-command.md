# 0037 — Enter on a highlighted completion runs that command

**Date:** 2026-04-25
**Status:** accepted

## Context

The slash-command completion popover (`CompletionMenu`) opens
above the command bar whenever the user types `/`. Through 0.10.0
the keybindings while the menu was open were:

- <kbd>↑</kbd> / <kbd>↓</kbd> — move the highlight.
- <kbd>Tab</kbd> — insert the highlighted command into the input
  with a trailing space.
- <kbd>Enter</kbd> — submit the input value as-is (the menu was
  effectively read-only at that moment).

Running a command therefore took two keystrokes: <kbd>Tab</kbd>
to insert, then <kbd>Enter</kbd> to submit. For commands that
take no arguments — the majority of the catalogue — the
intermediate insertion step is friction with no payoff.

This pattern matches generic editor autocomplete (where Enter
"completes in place"), but it isn't the right shape for a slash-
command launcher: the menu is purpose-built to pick a command
and run it, not to edit text. CLIs like fzf and shell history
search use Enter to run the highlighted entry directly; users
arrive expecting that behaviour.

## Decision

When the completion menu is open and a row is highlighted,
<kbd>Enter</kbd> runs the highlighted command directly. The
command bar's `on_key` handler swaps the typed prefix for the
selected name and lets `Input`'s default Enter handler fire
`Input.Submitted`, which the session screen routes through
`CommandRegistry.dispatch`. One keystroke runs the command.

<kbd>Tab</kbd> is unchanged. It still inserts the highlighted
name plus a trailing space so the user can type arguments before
submitting. Tab is now the explicit "I want to add args" path;
Enter is the "just run it" path.

If the menu is open but no row is highlighted (an edge case
reachable by clearing the registry while typing), <kbd>Enter</kbd>
falls through to the default submit on whatever the user typed.

## Consequences

**Easier:**

- The common case — running a zero-argument command — drops
  from two keystrokes to one. <kbd>/</kbd>, type the prefix,
  <kbd>Enter</kbd>.
- The keybinding map now reads as a clear pair: Tab when you
  need to type more, Enter when you're done. Neither key is a
  surprise.
- Behaviour matches established launcher tools (fzf-style
  pickers, shell history search) rather than text-editor
  autocomplete — the right reference frame for a command bar.

**Harder:**

- A user who types `/co`, sees `/context` highlighted, and
  presses <kbd>Enter</kbd> intending to submit `/co` as a typo'd
  unknown command will instead run `/context`. We accept this:
  unknown commands always emit an error banner and the
  highlighted row is visually obvious, so the surprise is
  short-lived. Users who do want the typed text submitted can
  press <kbd>Esc</kbd> first to dismiss the menu.
- Commands that *require* arguments (none today, `/ephemeral`
  is a placeholder) will fall through to the handler with an
  empty tail when launched via Enter. Handlers should print a
  usage banner; that's a per-command concern, not a binding
  concern.

**Ongoing cost:**

- None. The change is local to `CommandBar.on_key` and adds no
  new state.
