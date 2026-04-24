"""The real `ApprovalGateway` — pushes a modal, awaits the user's answer.

Bridges the orchestrator's tool-approval chain to the Textual UI.
Until this lands the orchestrator is wired with the stub
`AutoApproveGateway` / `DenyAllGateway` for headless runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.orchestrator._enums import ApprovalOutcome
from cairn.orchestrator._middleware import ApprovalDecision
from cairn.ui._screens._approval import ApprovalModal

if TYPE_CHECKING:
    from cairn.orchestrator._middleware import ApprovalRequest
    from cairn.tools._approvers import SessionAllowlist
    from cairn.ui._app import CairnApp


class TextualApprovalGateway:
    """`ApprovalGateway` implementation backed by `ApprovalModal`.

    Presents the modal, awaits the result, honours the "remember for
    this session" checkbox by calling
    `SessionAllowlist.remember_user_approval`.

    The gateway is constructed with a reference to the running
    `CairnApp` so it can `push_screen_wait` from whichever coroutine
    the orchestrator's approval chain runs on.
    """

    def __init__(
        self,
        *,
        app: CairnApp,
        session_allowlist: SessionAllowlist,
    ) -> None:
        self._app = app
        self._session_allowlist = session_allowlist

    async def request(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        del tool_call_id  # audit row carries it; gateway itself just needs the request
        modal = ApprovalModal(request=request)
        result = await self._app.push_screen_wait(modal)
        if result.outcome is ApprovalOutcome.APPROVE and result.remember:
            self._session_allowlist.remember_user_approval(
                session_id=session_id,
                request=request,
            )
        return ApprovalDecision(
            outcome=result.outcome,
            decided_by="user",
            reason=result.reason,
        )
