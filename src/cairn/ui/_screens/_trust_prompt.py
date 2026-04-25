"""Modal asking the user whether to trust a project's convention files.

Shown on first encounter when `trust_policy="prompt"`. Three
outcomes: trust once (this process only), trust the project
(persist to the allowlist), deny (skip loading).

The modal is kicked off by `TextualPromptTrustGate.check`; the
gateway itself owns the decision cache so repeated checks for the
same project within a process don't re-prompt.

Keyboard: ``o`` / ``p`` / ``n`` / ``escape`` are bound directly.
``left`` / ``right`` cycle button focus, and ``enter`` activates the
focused button. Initial focus lands on **Deny** so an accidental
Enter on a stranger's project never widens trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from cairn.conventions._trust import TrustDecision

if TYPE_CHECKING:
    from textual.app import ComposeResult


_MAX_DISPLAY_PATH_COMPONENTS = 4
_BUTTON_ORDER: tuple[str, ...] = ("once", "project", "deny")


@dataclass(frozen=True, slots=True)
class TrustPromptResult:
    """Payload returned from `TrustPromptModal` via `dismiss()`."""

    decision: TrustDecision
    persist: bool
    """True when the user chose "trust project" (add to allowlist)."""


class TrustPromptModal(ModalScreen[TrustPromptResult]):
    """Blocking modal for the three-way trust decision.

    Hotkeys: ``o`` trust-once, ``p`` trust-project, ``n`` / ``escape``
    deny. Buttons mirror the keys for mouse users; arrow keys cycle
    button focus and Enter activates the focused button.
    """

    BINDINGS: ClassVar = [
        ("o", "trust_once", "Trust once"),
        ("p", "trust_project", "Trust project"),
        ("n", "deny", "Deny"),
        ("escape", "deny", "Deny"),
        ("left", "focus_prev_button", "Previous"),
        ("right", "focus_next_button", "Next"),
    ]

    DEFAULT_CSS = """
    TrustPromptModal {
        align: center middle;
    }
    TrustPromptModal > Vertical {
        width: 80;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $panel;
    }
    TrustPromptModal .title {
        text-style: bold;
        padding-bottom: 1;
    }
    TrustPromptModal .meta {
        color: $text-muted;
    }
    TrustPromptModal .files {
        padding: 1 0;
        color: $text;
    }
    TrustPromptModal .buttons {
        padding-top: 1;
        align: center middle;
        height: auto;
    }
    TrustPromptModal Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        *,
        project_root: Path,
        files: list[Path],
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._files = files

    def compose(self) -> ComposeResult:
        display_path = _format_project_path(self._project_root)
        sorted_names = sorted({f.name for f in self._files}) or ["(no files)"]
        files_block = "\n".join(f"• {name}" for name in sorted_names)

        with Vertical():
            yield Label("Trust project convention files?", classes="title")
            yield Label(f"Project: {display_path}", classes="meta")
            yield Label(files_block, classes="files", id="files-list")
            yield Label(
                "Convention files inject instructions into the model. "
                "Only trust projects you know.",
                classes="meta",
            )
            with Horizontal(classes="buttons"):
                yield Button("Trust once (o)", variant="primary", id="once")
                yield Button("Trust project (p)", variant="success", id="project")
                yield Button("Deny (n)", variant="error", id="deny")

    def on_mount(self) -> None:
        # Default focus on Deny: an accidental Enter on a stranger's
        # project should never widen trust.
        self.query_one("#deny", Button).focus()

    # -- Actions --------------------------------------------------------

    def action_trust_once(self) -> None:
        self._complete(TrustDecision.ALLOW, persist=False)

    def action_trust_project(self) -> None:
        self._complete(TrustDecision.ALLOW, persist=True)

    def action_deny(self) -> None:
        self._complete(TrustDecision.DENY, persist=False)

    def action_focus_next_button(self) -> None:
        self._move_button_focus(1)

    def action_focus_prev_button(self) -> None:
        self._move_button_focus(-1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "once":
            self.action_trust_once()
        elif event.button.id == "project":
            self.action_trust_project()
        elif event.button.id == "deny":
            self.action_deny()

    # -- Internal -------------------------------------------------------

    def _complete(self, decision: TrustDecision, *, persist: bool) -> None:
        self.dismiss(TrustPromptResult(decision=decision, persist=persist))

    def _move_button_focus(self, delta: int) -> None:
        focused = self.focused
        current_id = getattr(focused, "id", None) if focused is not None else None
        try:
            idx = _BUTTON_ORDER.index(current_id) if current_id else -1
        except ValueError:
            idx = -1
        new_idx = (idx + delta) % len(_BUTTON_ORDER)
        target = self.query_one(f"#{_BUTTON_ORDER[new_idx]}", Button)
        target.focus()


def _format_project_path(path: Path) -> str:
    """Render an absolute path with only its last few components.

    Keeps the modal readable for deeply-nested projects without
    hiding which project the user is being asked about. Preserves
    the full path when it's short enough to fit.
    """
    parts = path.parts
    if len(parts) <= _MAX_DISPLAY_PATH_COMPONENTS:
        return str(path)
    tail = Path(*parts[-_MAX_DISPLAY_PATH_COMPONENTS:])
    return f".../{tail}"
