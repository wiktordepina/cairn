"""Tests for the built-in `BudgetOverflowGateway` stubs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cairn.compaction import (
    AutoContinueOverflowGateway,
    AutoTerminateOverflowGateway,
    BudgetOverflowGateway,
)
from cairn.domain import BudgetOverflowAdvisory, Session
from cairn.domain._enums import SessionType
from cairn.orchestrator._context import TurnContext


def _advisory() -> BudgetOverflowAdvisory:
    return BudgetOverflowAdvisory(
        session_id="sess-x",
        turn_id="turn-x",
        tokens_projected=210_000,
        context_window=200_000,
        safety_margin=2048,
        overflow_tokens=12_048,
        will_fit_context_window=False,
    )


def _ctx() -> TurnContext:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    session = Session(
        id="sess-x",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        title=None,
        archived=False,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )
    return TurnContext(session=session, turn_id="turn-x", iteration=0)


@pytest.mark.asyncio
async def test_auto_continue_gateway_returns_continue() -> None:
    gateway = AutoContinueOverflowGateway()
    assert await gateway.decide(_advisory(), _ctx()) == "continue"


@pytest.mark.asyncio
async def test_auto_terminate_gateway_returns_terminate() -> None:
    gateway = AutoTerminateOverflowGateway()
    assert await gateway.decide(_advisory(), _ctx()) == "terminate"


def test_gateway_stubs_satisfy_protocol() -> None:
    assert isinstance(AutoContinueOverflowGateway(), BudgetOverflowGateway)
    assert isinstance(AutoTerminateOverflowGateway(), BudgetOverflowGateway)
