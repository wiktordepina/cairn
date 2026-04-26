"""Generic N-option choice modal.

Shown when a slash command needs the user to pick between two or
three short, named options (e.g. ``/model`` "keep history /
start fresh / abort", ``/archive`` "archive and quit / cancel").

Each option carries a single hotkey character; pressing the key is
equivalent to pressing the option's button. ``Esc`` always
dismisses with ``None`` (treated as "cancel" by callers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual import events
    from textual.app import ComposeResult


@dataclass(frozen=True, slots=True)
class Choice:
    """A single option in a `ChoiceModal`.

    `key` is the single-character hotkey used as the option's stable
    id and returned via `dismiss()` when the user picks it. `label`
    is the button caption (the hotkey is appended in parentheses).
    `variant` maps to a Textual button variant.
    """

    key: str
    label: str
    variant: str = "default"


class ChoiceModal(ModalScreen[str | None]):
    """Modal asking the user to pick one of N short options.

    Returns the picked `Choice.key` via `dismiss()`, or `None` on Esc.
    Initial focus lands on the first option whose `key` matches
    `default_key`, or the first option overall when `default_key` is
    `None` or unrecognised.
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
        Binding("left", "focus_prev_button", "Previous", show=False),
        Binding("right", "focus_next_button", "Next", show=False),
    ]

    DEFAULT_CSS = """
    ChoiceModal {
        align: center middle;
    }
    ChoiceModal > Vertical {
        width: 80;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $panel;
    }
    ChoiceModal .title {
        text-style: bold;
        padding-bottom: 1;
    }
    ChoiceModal .body {
        padding-bottom: 1;
        color: $text;
    }
    ChoiceModal .buttons {
        padding-top: 1;
        align: center middle;
        height: auto;
    }
    ChoiceModal Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        choices: Sequence[Choice],
        default_key: str | None = None,
    ) -> None:
        super().__init__()
        if not choices:
            raise ValueError("ChoiceModal requires at least one choice.")
        seen_keys: set[str] = set()
        for c in choices:
            if not c.key or len(c.key) != 1:
                raise ValueError(f"Choice keys must be single characters: {c.key!r}")
            if c.key in seen_keys:
                raise ValueError(f"Duplicate choice key: {c.key!r}")
            seen_keys.add(c.key)
        self._title = title
        self._body = body
        self._choices = tuple(choices)
        self._default_key = default_key

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, classes="title")
            yield Label(self._body, classes="body")
            with Horizontal(classes="buttons"):
                for choice in self._choices:
                    yield Button(
                        f"{choice.label} ({choice.key})",
                        variant=choice.variant,  # type: ignore[arg-type]
                        id=f"choice-{choice.key}",
                    )

    def on_mount(self) -> None:
        keys = [c.key for c in self._choices]
        target = self._default_key if self._default_key in keys else keys[0]
        self._focus_choice(target)

    def on_key(self, event: events.Key) -> None:
        # Hotkey form: each Choice carries a single character; pressing
        # it dismisses the modal with that key. Buttons remain
        # navigable via arrow keys + Enter for mouse / sighted users.
        if event.key in {c.key for c in self._choices}:
            event.stop()
            self.dismiss(event.key)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_focus_next_button(self) -> None:
        self._cycle_button_focus(1)

    def action_focus_prev_button(self) -> None:
        self._cycle_button_focus(-1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("choice-"):
            self.dismiss(button_id[len("choice-") :])

    def _focus_choice(self, key: str) -> None:
        self.query_one(f"#choice-{key}", Button).focus()

    def _cycle_button_focus(self, delta: int) -> None:
        keys = [c.key for c in self._choices]
        focused = self.focused
        current_id = getattr(focused, "id", None) if focused is not None else None
        current_key: str | None = None
        if current_id and current_id.startswith("choice-"):
            current_key = current_id[len("choice-") :]
        idx = keys.index(current_key) if current_key in keys else -1
        new_idx = (idx + delta) % len(keys)
        self._focus_choice(keys[new_idx])
