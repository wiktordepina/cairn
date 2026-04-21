"""The default ``ToolRunner`` implementation.

Replaces ``orchestrator.RaisingToolRunner``. Follows the design in
``.plan/tool-system-design.md`` §7, Option A: the orchestrator owns the
approval chain and emits UI events; the runner receives the already-made
``ApprovalDecision`` and handles the execute + persist + classify path.

Per-call flow:

    1. ``tool_call_repo.start(pending)`` — audit the attempt.
    2. Write the approval outcome to ``approval_decisions``.
    3a. On REJECT: ``tool_call_repo.reject()`` → synthesised error
        ``ToolResultBlock``; return.
    3b. On APPROVE: ``tool_call_repo.approve()``.
    4. ``tool_call_repo.mark_executing()``.
    5. ``asyncio.timeout(tool.timeout_s)`` around ``tool.invoke(args, ctx)``.
    6. On success: ``tool_call_repo.complete()`` → return result.
       On timeout: ``tool_call_repo.mark_timed_out()`` → error result.
       On other exception: ``tool_call_repo.mark_failed()`` → error result.

The orchestrator still wraps the returned result through the
``ResultTransformer`` chain before yielding it upstream; the runner
itself does not apply transformers in this iteration (keeps the Phase 7
integration diff minimal — transformer ownership may migrate later).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Literal

from cairn.domain._content import ToolResultBlock
from cairn.domain._enums import ErrorClass
from cairn.orchestrator._enums import ApprovalOutcome
from cairn.tools._errors import (
    PathEscape,
    SSRFBlocked,
    ToolError,
    ToolTimeout,
)

if TYPE_CHECKING:
    from cairn.domain._content import ToolUseBlock
    from cairn.domain._sessions import Session
    from cairn.orchestrator._clock import Clock
    from cairn.orchestrator._context import TurnContext
    from cairn.orchestrator._middleware import ApprovalDecision
    from cairn.orchestrator._protocols import Tool
    from cairn.persistence._approval_repo import ApprovalDecisionRepo
    from cairn.persistence._tool_calls_repo import ToolCallRepo


class DefaultToolRunner:
    """Execute a single approved tool call, recording every transition.

    Instantiated once per session-scoped dispatch stack and shared across
    turns. All DB writes go through the injected repositories; the clock
    is injected so tests can freeze time.
    """

    def __init__(
        self,
        *,
        tool_call_repo: ToolCallRepo,
        approval_repo: ApprovalDecisionRepo,
        clock: Clock,
    ) -> None:
        self._tool_call_repo = tool_call_repo
        self._approval_repo = approval_repo
        self._clock = clock

    async def run(
        self,
        *,
        tool_call: ToolUseBlock,
        tool: Tool,
        session: Session,
        turn_id: str,  # noqa: ARG002 — reserved; plumbed through for future FKs
        ctx: TurnContext,
        decision: ApprovalDecision,
        message_id: str,
    ) -> ToolResultBlock:
        started_at = self._clock.now()
        args: dict[str, object] = dict(tool_call.input)
        input_json = json.dumps(args, sort_keys=True, default=str)

        await self._tool_call_repo.start(
            id=tool_call.id,
            session_id=session.id,
            message_id=message_id,
            tool_name=tool.name,
            tool_kind=tool.tool_kind,
            input_json=input_json,
            approval_required=tool.approval_required,
            is_delegation=(tool.tool_kind == "delegation"),
            started_at=started_at,
        )

        await self._approval_repo.record(
            tool_call_id=tool_call.id,
            decided_at=started_at,
            decided_by=decision.decided_by,
            decision=(
                "approved"
                if decision.outcome is ApprovalOutcome.APPROVE
                else "rejected"
            ),
            reason=decision.reason,
            args_snapshot_json=input_json,
        )

        if decision.outcome is ApprovalOutcome.REJECT:
            await self._tool_call_repo.reject(
                tool_call.id,
                reason=decision.reason,
            )
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=decision.reason or "Tool rejected by approver.",
                is_error=True,
            )

        await self._tool_call_repo.approve(
            tool_call.id,
            approved_by=_narrow_approver(decision.decided_by),
            approved_at=started_at,
        )
        await self._tool_call_repo.mark_executing(tool_call.id)

        try:
            async with asyncio.timeout(tool.timeout_s):
                result = await tool.invoke(args, ctx)
        except TimeoutError:
            await self._tool_call_repo.mark_timed_out(tool_call.id)
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"Tool {tool.name!r} timed out after {tool.timeout_s}s.",
                is_error=True,
            )
        except (PathEscape, SSRFBlocked, ToolTimeout, ToolError) as exc:
            await self._tool_call_repo.mark_failed(
                tool_call.id,
                error_class=ErrorClass.USER,
                error_message=str(exc),
            )
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=str(exc),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            await self._tool_call_repo.mark_failed(
                tool_call.id,
                error_class=ErrorClass.UNEXPECTED,
                error_message=str(exc),
            )
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"Tool runner error: {exc}",
                is_error=True,
            )

        # Tool implementations return results with an empty tool_use_id
        # (the ``@tool`` decorator + ``DelegationTool`` both follow this
        # convention); the runner is responsible for stamping the call id
        # on the way back so the provider can correlate it to the tool_use.
        if not result.tool_use_id:
            result = result.model_copy(update={"tool_use_id": tool_call.id})

        output_json = result.model_dump_json()
        await self._tool_call_repo.complete(
            tool_call.id,
            output_json=output_json,
            output_bytes=len(output_json.encode("utf-8")),
            completed_at=self._clock.now(),
        )
        return result


def _narrow_approver(decided_by: str) -> Literal["user", "auto"]:
    """Map the fine-grained ``decided_by`` to the narrow DB enum.

    ``ToolCallRepo.approve`` accepts only ``"user"`` or ``"auto"``; the
    approver chain emits richer strings (``"auto:read-only"``,
    ``"user:session-allowlist"``). We split on the prefix.
    """
    return "auto" if decided_by.startswith("auto") else "user"
