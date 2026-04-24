"""Modal asking the user whether to trust a project's convention files.

Shown on first encounter when `trust_policy="prompt"`. Three
outcomes: trust once (this process only), trust the project
(persist to the allowlist), deny (skip loading).

The modal is kicked off by `TextualPromptTrustGate.check`; the
gateway itself owns the decision cache so repeated checks for the
same project within a process don't re-prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from cairn.conventions._trust import TrustDecision

if TYPE_CHECKING:
    from textual.app import ComposeResult


_PREVIEW_LINES = 20
_MAX_DISPLAY_PATH_COMPONENTS = 4


@dataclass(frozen=True, slots=True)
class TrustPromptResult:
    """Payload returned from `TrustPromptModal` via `dismiss()`."""

    decision: TrustDecision
    persist: bool
    """True when the user chose "trust project" (add to allowlist)."""


class TrustPromptModal(ModalScreen[TrustPromptResult]):
    """Blocking modal for the three-way trust decision.

    Keybindings: `o` trust-once, `p` trust-project, `n` / `escape`
    deny. Buttons mirror the keys so mouse users aren't excluded.
    """

    BINDINGS = [
        ("o", "trust_once", "Trust once"),
        ("p", "trust_project", "Trust project"),
        ("n", "deny", "Deny"),
        ("escape", "deny", "Deny"),
    ]

    DEFAULT_CSS = """
    TrustPromptModal {
        align: center middle;
    }
    TrustPromptModal > Vertical {
        width: 90;
        max-height: 34;
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
    TrustPromptModal .preview {
        padding: 1 0;
        max-height: 20;
        overflow-y: auto;
        color: $text;
    }
    TrustPromptModal .buttons {
        padding-top: 1;
        align: center middle;
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
        preview_body: str,
        preview_truncated: bool,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._files = files
        self._preview_body = preview_body
        self._preview_truncated = preview_truncated

    def compose(self) -> ComposeResult:
        display_path = _format_project_path(self._project_root)
        filenames = ", ".join(sorted({f.name for f in self._files}))
        preview = self._preview_body
        if self._preview_truncated:
            preview = preview + "\n…"

        with Vertical():
            yield Label("Trust project convention files?", classes="title")
            yield Label(f"Project: {display_path}", classes="meta")
            yield Label(f"Files: {filenames}", classes="meta")
            yield Label("")
            yield Static(preview, classes="preview", id="preview-body")
            yield Label(
                "Convention files inject instructions into the model. "
                "Only trust projects you know.",
                classes="meta",
            )
            with Horizontal(classes="buttons"):
                yield Button("Trust once (o)", variant="primary", id="once")
                yield Button("Trust project (p)", variant="success", id="project")
                yield Button("Deny (n)", variant="error", id="deny")

    # -- Actions --------------------------------------------------------

    def action_trust_once(self) -> None:
        self._complete(TrustDecision.ALLOW, persist=False)

    def action_trust_project(self) -> None:
        self._complete(TrustDecision.ALLOW, persist=True)

    def action_deny(self) -> None:
        self._complete(TrustDecision.DENY, persist=False)

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


def preview_file_lines(text: str, *, max_lines: int = _PREVIEW_LINES) -> tuple[str, bool]:
    """Return the first *max_lines* of *text* + a truncation flag.

    Exposed for tests + the gateway that assembles the modal input.
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines), False
    return "\n".join(lines[:max_lines]), True
