"""Modal prompting the user to approve or reject a tool call.

The same modal handles both tier-3 (SessionAllowlist first-run) and
tier-4+ (always-prompt) flows. Per resolved Q3, args render as a
truncated top-level list by default; tier-4+ auto-expands nested
structures in full. Per resolved ADR 0035, the remember checkbox
is disabled for tier-4+ because those are explicit-approval every
time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Static

from cairn.orchestrator._enums import ApprovalOutcome

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from cairn.orchestrator._middleware import ApprovalRequest


_TIER_4_PLUS = 4
_ARG_VALUE_TRUNCATE = 200


@dataclass(frozen=True, slots=True)
class ApprovalModalResult:
    """Payload returned from `ApprovalModal` via `dismiss()`."""

    outcome: ApprovalOutcome
    remember: bool
    reason: str | None


class ApprovalModal(ModalScreen[ApprovalModalResult]):
    """Blocking modal for tool-call approval.

    Shows the tool name, risk tier, side-effects label, and rendered
    args. Approving returns `outcome=APPROVE`; rejecting returns
    `outcome=REJECT` with `reason="user_rejected"`; dismissing via
    escape returns `REJECT` with `reason="user_dismissed"`.
    """

    BINDINGS = [
        ("escape", "dismiss_reject", "Reject"),
        ("y", "approve", "Approve"),
        ("n", "reject", "Reject"),
    ]

    DEFAULT_CSS = """
    ApprovalModal {
        align: center middle;
    }
    ApprovalModal > Vertical {
        width: 80;
        max-height: 30;
        padding: 1 2;
        border: thick $accent;
        background: $panel;
    }
    ApprovalModal .title {
        text-style: bold;
        padding-bottom: 1;
    }
    ApprovalModal .meta {
        color: $text-muted;
        padding-bottom: 1;
    }
    ApprovalModal .args {
        padding: 1 0;
        max-height: 14;
        overflow-y: auto;
    }
    ApprovalModal .buttons {
        padding-top: 1;
        align: center middle;
    }
    ApprovalModal Button {
        margin: 0 1;
    }
    """

    def __init__(self, *, request: ApprovalRequest) -> None:
        super().__init__()
        self._request = request
        self._tier_4_plus = request.risk_tier >= _TIER_4_PLUS

    @property
    def request(self) -> ApprovalRequest:
        return self._request

    def compose(self) -> ComposeResult:
        req = self._request
        with Vertical():
            yield Label(f"Approve tool call: {req.tool_name}", classes="title")
            yield Label(
                f"Tier {req.risk_tier} · side-effects: {req.side_effects}",
                classes="meta",
            )
            yield Static(self._render_args(), classes="args", id="args-body")
            yield Checkbox(
                "Remember this exact call for the session",
                value=False,
                id="remember",
                disabled=self._tier_4_plus,
            )
            if self._tier_4_plus:
                yield Label(
                    "(tier-4+ tools require explicit approval every time)",
                    classes="meta",
                )
            with Horizontal(classes="buttons"):
                yield Button("Approve (y)", variant="success", id="approve")
                yield Button("Reject (n)", variant="error", id="reject")

    # -- Actions --------------------------------------------------------

    def action_approve(self) -> None:
        self._complete(ApprovalOutcome.APPROVE, reason=None)

    def action_reject(self) -> None:
        self._complete(ApprovalOutcome.REJECT, reason="user_rejected")

    def action_dismiss_reject(self) -> None:
        self._complete(ApprovalOutcome.REJECT, reason="user_dismissed")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.action_approve()
        elif event.button.id == "reject":
            self.action_reject()

    # -- Internal -------------------------------------------------------

    def _complete(self, outcome: ApprovalOutcome, *, reason: str | None) -> None:
        remember = self._remember_checked() if outcome is ApprovalOutcome.APPROVE else False
        self.dismiss(ApprovalModalResult(outcome=outcome, remember=remember, reason=reason))

    def _remember_checked(self) -> bool:
        if self._tier_4_plus:
            return False
        checkbox = self.query_one("#remember", Checkbox)
        return checkbox.value

    def _render_args(self) -> str:
        """Render the args dict for display.

        Tier ≤ 3: top-level key: value list, each value truncated at
        200 chars. Tier ≥ 4: full pretty-printed JSON so the user can
        see exactly what's being approved.
        """
        args = self._request.args
        if not args:
            return "(no arguments)"
        if self._tier_4_plus:
            return json.dumps(args, indent=2, default=str)
        lines: list[str] = []
        for key, value in args.items():
            rendered = json.dumps(value, default=str)
            if len(rendered) > _ARG_VALUE_TRUNCATE:
                rendered = rendered[:_ARG_VALUE_TRUNCATE] + "…"
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines)
