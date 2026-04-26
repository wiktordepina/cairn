"""Pilot tests for the generic `ChoiceModal`.

The same harness pattern as `test_approval.py`: wrap the
`push_screen_wait` call in a worker so the modal can run while the
test issues key presses through the Pilot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._screens._choice_modal import Choice, ChoiceModal

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


def _three_options() -> tuple[Choice, ...]:
    return (
        Choice(key="k", label="Keep history", variant="primary"),
        Choice(key="s", label="Start fresh", variant="warning"),
        Choice(key="a", label="Abort"),
    )


class TestChoiceModal:
    @pytest.mark.asyncio
    async def test_hotkey_dismisses_with_choice(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[str | None] = []

            async def inner() -> None:
                holder.append(
                    await app.push_screen_wait(
                        ChoiceModal(
                            title="Switch model",
                            body="N messages — pick a strategy.",
                            choices=_three_options(),
                            default_key="k",
                        )
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("s")
            await worker.wait()

            assert holder == ["s"]

    @pytest.mark.asyncio
    async def test_escape_dismisses_with_none(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[str | None] = []

            async def inner() -> None:
                holder.append(
                    await app.push_screen_wait(
                        ChoiceModal(
                            title="Confirm",
                            body="Are you sure?",
                            choices=(
                                Choice(key="y", label="Yes", variant="primary"),
                                Choice(key="n", label="No"),
                            ),
                            default_key="n",
                        )
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("escape")
            await worker.wait()

            assert holder == [None]

    def test_rejects_empty_choices(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            ChoiceModal(title="t", body="b", choices=())

    def test_rejects_duplicate_keys(self) -> None:
        with pytest.raises(ValueError, match="Duplicate"):
            ChoiceModal(
                title="t",
                body="b",
                choices=(Choice(key="x", label="X"), Choice(key="x", label="X2")),
            )

    def test_rejects_multi_char_keys(self) -> None:
        with pytest.raises(ValueError, match="single characters"):
            ChoiceModal(
                title="t",
                body="b",
                choices=(Choice(key="ab", label="A"),),
            )
