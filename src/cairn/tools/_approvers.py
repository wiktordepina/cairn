"""Approver middleware shipped with the tool system.

Registered into the orchestrator's `approvers` chain at
harness-assembly time. Default order (config-overridable):

    AutoApproveReadOnly → SessionAllowlist → TierGate → ApprovalGateway

The chain short-circuits on first non-ESCALATE decision. Tier-4+
calls escalate all the way to the gateway (typically the UI) even if
earlier approvers fire.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import TYPE_CHECKING

from cairn.orchestrator._enums import ApprovalOutcome
from cairn.orchestrator._middleware import ApprovalDecision, ApprovalRequest

if TYPE_CHECKING:
    from cairn.orchestrator._context import TurnContext


# ---------------------------------------------------------------------------
# AutoApproveReadOnly
# ---------------------------------------------------------------------------


class AutoApproveReadOnly:
    """Approves any tool call that's read-only and tier <= 2.

    Corresponds to security doc §5 rows for Tier 0 (pure compute),
    Tier 1 (scoped read), and Tier 2 (external read — approved with
    a visible log, which is handled downstream by the observer chain).
    """

    async def decide(
        self,
        request: ApprovalRequest,
        ctx: TurnContext,  # noqa: ARG002
    ) -> ApprovalDecision:
        if request.side_effects in ("none", "read") and request.risk_tier <= 2:
            return ApprovalDecision(
                outcome=ApprovalOutcome.APPROVE,
                decided_by="auto:read-only",
            )
        return ApprovalDecision(
            outcome=ApprovalOutcome.ESCALATE,
            decided_by="auto:read-only",
        )


# ---------------------------------------------------------------------------
# SessionAllowlist
# ---------------------------------------------------------------------------


class SessionAllowlist:
    """Remembers user approvals within a session to avoid re-prompting.

    Exact-match on `(tool_name, args_signature)`. First-run of a
    call prompts (via the gateway); second and subsequent identical
    calls are auto-approved by this approver.

    The harness-assembly layer is responsible for calling
    `remember_user_approval(session_id, request)` when an approval
    comes back from the gateway with `decided_by='user'`. In
    typical wiring: the orchestrator, after receiving a user approval,
    calls this method on the shared `SessionAllowlist` instance.
    """

    def __init__(self) -> None:
        # session_id → set of approved (tool_name, args_sig) strings.
        self._seen: dict[str, set[str]] = defaultdict(set)

    async def decide(
        self,
        request: ApprovalRequest,
        ctx: TurnContext,
    ) -> ApprovalDecision:
        key = self._key(request)
        if key in self._seen[ctx.session.id]:
            return ApprovalDecision(
                outcome=ApprovalOutcome.APPROVE,
                decided_by="auto:session-allowlist",
            )
        return ApprovalDecision(
            outcome=ApprovalOutcome.ESCALATE,
            decided_by="auto:session-allowlist",
        )

    def remember_user_approval(self, *, session_id: str, request: ApprovalRequest) -> None:
        """Cache `request` as approved for `session_id`."""
        self._seen[session_id].add(self._key(request))

    def forget_session(self, session_id: str) -> None:
        """Drop all approvals for `session_id`. Call on session archive."""
        self._seen.pop(session_id, None)

    @staticmethod
    def _key(request: ApprovalRequest) -> str:
        args_json = json.dumps(request.args, sort_keys=True, default=str)
        args_sig = hashlib.sha256(args_json.encode("utf-8")).hexdigest()
        return f"{request.tool_name}:{args_sig}"


# ---------------------------------------------------------------------------
# TierGate
# ---------------------------------------------------------------------------


class TierGate:
    """Escalates Tier 4+ calls to the gateway unconditionally.

    Runs last in the chain. Defends against a misconfigured earlier
    approver (e.g. a buggy `SessionAllowlist` snapshot) that might
    otherwise auto-approve a high-risk call. For Tier <= 3, escalates
    so earlier decisions stand.
    """

    async def decide(
        self,
        request: ApprovalRequest,
        ctx: TurnContext,  # noqa: ARG002
    ) -> ApprovalDecision:
        # Chain order: if an earlier approver already decided APPROVE,
        # TierGate never runs (the orchestrator short-circuits on the
        # first non-ESCALATE). When TierGate does run, it means either
        # earlier approvers escalated or this is the only approver.
        # Return ESCALATE so the gateway handles it. That gives Tier 4+
        # its required "every-call prompt" without special-casing.
        if request.risk_tier >= 4:
            return ApprovalDecision(
                outcome=ApprovalOutcome.ESCALATE,
                decided_by="tier-gate",
            )
        return ApprovalDecision(
            outcome=ApprovalOutcome.ESCALATE,
            decided_by="tier-gate",
        )
