"""Pilot tests for the `ApprovalModal` and `TextualApprovalGateway`.

`push_screen_wait` requires a Textual worker context (production
uses `@work` on the turn coroutine, per arch-doc §4.14). Tests
wrap the modal/gateway calls in `app.run_worker()` to satisfy that
requirement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from textual.widgets import Checkbox

from cairn.orchestrator._context import TurnContext
from cairn.orchestrator._enums import ApprovalOutcome
from cairn.orchestrator._middleware import ApprovalDecision, ApprovalRequest
from cairn.tools._approvers import SessionAllowlist
from cairn.ui._app import CairnApp
from cairn.ui._gateway import TextualApprovalGateway
from cairn.ui._screens._approval import ApprovalModal, ApprovalModalResult

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


def _request(
    *,
    tier: int = 3,
    side_effects: str = "write",
    args: dict[str, object] | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        tool_call_id="tc-1",
        tool_name="file_write",
        args=args or {"path": "/tmp/x", "content": "hello"},
        risk_tier=tier,
        side_effects=cast("Any", side_effects),
    )


class TestApprovalModal:
    @pytest.mark.asyncio
    async def test_approve_returns_approve_outcome(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[ApprovalModalResult] = []

            async def inner() -> None:
                holder.append(await app.push_screen_wait(ApprovalModal(request=_request())))

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("y")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.APPROVE
            assert holder[0].remember is False
            assert holder[0].reason is None

    @pytest.mark.asyncio
    async def test_reject_returns_user_rejected(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[ApprovalModalResult] = []

            async def inner() -> None:
                holder.append(await app.push_screen_wait(ApprovalModal(request=_request())))

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("n")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.REJECT
            assert holder[0].reason == "user_rejected"

    @pytest.mark.asyncio
    async def test_escape_is_user_dismissed(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[ApprovalModalResult] = []

            async def inner() -> None:
                holder.append(await app.push_screen_wait(ApprovalModal(request=_request())))

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("escape")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.REJECT
            assert holder[0].reason == "user_dismissed"

    @pytest.mark.asyncio
    async def test_tier_4_disables_remember_checkbox(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[ApprovalModalResult] = []

            async def inner() -> None:
                holder.append(await app.push_screen_wait(ApprovalModal(request=_request(tier=4))))

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ApprovalModal)

            checkbox = modal.query_one("#remember", Checkbox)
            assert checkbox.disabled is True

            await pilot.press("y")
            await worker.wait()

            # Even if somehow ticked, tier-4+ forces remember=False.
            assert holder[0].outcome is ApprovalOutcome.APPROVE
            assert holder[0].remember is False

    @pytest.mark.asyncio
    async def test_tier_3_remember_checkbox_enabled_and_carries_through(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[ApprovalModalResult] = []

            async def inner() -> None:
                holder.append(await app.push_screen_wait(ApprovalModal(request=_request(tier=3))))

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ApprovalModal)

            checkbox = modal.query_one("#remember", Checkbox)
            assert checkbox.disabled is False
            checkbox.value = True
            await pilot.press("y")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.APPROVE
            assert holder[0].remember is True


class TestTextualApprovalGateway:
    @pytest.mark.asyncio
    async def test_approve_flow_returns_user_decision(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        allowlist = SessionAllowlist()
        gateway = TextualApprovalGateway(app=app, session_allowlist=allowlist)

        async with app.run_test() as pilot:
            await pilot.pause()
            req = _request()
            holder: list[ApprovalDecision] = []

            async def inner() -> None:
                holder.append(
                    await gateway.request(
                        session_id=companion_session.id,
                        tool_call_id="tc-1",
                        request=req,
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("y")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.APPROVE
            assert holder[0].decided_by == "user"

    @pytest.mark.asyncio
    async def test_remember_path_records_allowlist_entry(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        allowlist = SessionAllowlist()
        gateway = TextualApprovalGateway(app=app, session_allowlist=allowlist)

        async with app.run_test() as pilot:
            await pilot.pause()
            req = _request(tier=3)
            holder: list[ApprovalDecision] = []

            async def inner() -> None:
                holder.append(
                    await gateway.request(
                        session_id=companion_session.id,
                        tool_call_id="tc-1",
                        request=req,
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ApprovalModal)
            modal.query_one("#remember", Checkbox).value = True
            await pilot.press("y")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.APPROVE

            # Second identical request should auto-approve via allowlist.
            ctx = TurnContext(session=companion_session, turn_id="t-2", iteration=0)
            second = await allowlist.decide(req, ctx)
            assert second.outcome is ApprovalOutcome.APPROVE
            assert second.decided_by == "auto:session-allowlist"

    @pytest.mark.asyncio
    async def test_reject_does_not_record_allowlist(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        allowlist = SessionAllowlist()
        gateway = TextualApprovalGateway(app=app, session_allowlist=allowlist)

        async with app.run_test() as pilot:
            await pilot.pause()
            req = _request(tier=3)
            holder: list[ApprovalDecision] = []

            async def inner() -> None:
                holder.append(
                    await gateway.request(
                        session_id=companion_session.id,
                        tool_call_id="tc-1",
                        request=req,
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ApprovalModal)
            # User ticks remember but then rejects — must NOT be
            # remembered.
            modal.query_one("#remember", Checkbox).value = True
            await pilot.press("n")
            await worker.wait()

            assert holder[0].outcome is ApprovalOutcome.REJECT

            ctx = TurnContext(session=companion_session, turn_id="t-2", iteration=0)
            second = await allowlist.decide(req, ctx)
            # ESCALATE (not remembered) — allowlist is empty.
            assert second.outcome is ApprovalOutcome.ESCALATE
