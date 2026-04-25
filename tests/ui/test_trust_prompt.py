"""Pilot + unit tests for the trust-prompt modal and gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.conventions._trust import AllowlistStore, TrustDecision
from cairn.ui._app import CairnApp
from cairn.ui._screens._trust_prompt import (
    TrustPromptModal,
    TrustPromptResult,
)
from cairn.ui._trust_gate import TextualPromptTrustGate

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


# ---------------------------------------------------------------------------
# Pilot: TrustPromptModal outcomes
# ---------------------------------------------------------------------------


class TestTrustPromptModal:
    @pytest.mark.asyncio
    async def test_trust_once_returns_allow_without_persist(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        app = _app_for(companion_session)
        result_holder: list[TrustPromptResult] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _show() -> None:
                result = await app.push_screen_wait(
                    TrustPromptModal(
                        project_root=tmp_path,
                        files=[tmp_path / "AGENTS.md"],
                    )
                )
                result_holder.append(result)

            app.run_worker(_show(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            await pilot.pause()

        assert len(result_holder) == 1
        assert result_holder[0].decision is TrustDecision.ALLOW
        assert result_holder[0].persist is False

    @pytest.mark.asyncio
    async def test_trust_project_returns_allow_and_persist(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        app = _app_for(companion_session)
        result_holder: list[TrustPromptResult] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _show() -> None:
                result = await app.push_screen_wait(
                    TrustPromptModal(
                        project_root=tmp_path,
                        files=[tmp_path / "AGENTS.md"],
                    )
                )
                result_holder.append(result)

            app.run_worker(_show(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()

        assert result_holder[0].decision is TrustDecision.ALLOW
        assert result_holder[0].persist is True

    @pytest.mark.asyncio
    async def test_deny_returns_deny(self, companion_session: Session, tmp_path: Path) -> None:
        app = _app_for(companion_session)
        result_holder: list[TrustPromptResult] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _show() -> None:
                result = await app.push_screen_wait(
                    TrustPromptModal(
                        project_root=tmp_path,
                        files=[tmp_path / "AGENTS.md"],
                    )
                )
                result_holder.append(result)

            app.run_worker(_show(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.pause()

        assert result_holder[0].decision is TrustDecision.DENY
        assert result_holder[0].persist is False

    @pytest.mark.asyncio
    async def test_default_focus_is_deny_button(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        """Initial focus on Deny so a stray Enter never widens trust."""
        app = _app_for(companion_session)

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = TrustPromptModal(
                project_root=tmp_path,
                files=[tmp_path / "AGENTS.md"],
            )
            app.push_screen(modal)
            await pilot.pause()

            focused = app.focused
            assert focused is not None
            assert getattr(focused, "id", None) == "deny"

    @pytest.mark.asyncio
    async def test_left_right_cycle_button_focus(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        """Left / Right walk between the three buttons."""
        app = _app_for(companion_session)

        async with app.run_test() as pilot:
            await pilot.pause()
            modal = TrustPromptModal(
                project_root=tmp_path,
                files=[tmp_path / "AGENTS.md"],
            )
            app.push_screen(modal)
            await pilot.pause()

            assert getattr(app.focused, "id", None) == "deny"
            await pilot.press("right")
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "once"
            await pilot.press("right")
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "project"
            await pilot.press("left")
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "once"

    @pytest.mark.asyncio
    async def test_enter_activates_focused_button(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        """Arrow-walk to a button and press Enter to activate it."""
        app = _app_for(companion_session)
        result_holder: list[TrustPromptResult] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _show() -> None:
                result = await app.push_screen_wait(
                    TrustPromptModal(
                        project_root=tmp_path,
                        files=[tmp_path / "AGENTS.md"],
                    )
                )
                result_holder.append(result)

            app.run_worker(_show(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            # Deny → Once → Project, then Enter.
            await pilot.press("right")
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

        assert result_holder[0].decision is TrustDecision.ALLOW
        assert result_holder[0].persist is True

    @pytest.mark.asyncio
    async def test_escape_is_deny(self, companion_session: Session, tmp_path: Path) -> None:
        app = _app_for(companion_session)
        result_holder: list[TrustPromptResult] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _show() -> None:
                result = await app.push_screen_wait(
                    TrustPromptModal(
                        project_root=tmp_path,
                        files=[tmp_path / "AGENTS.md"],
                    )
                )
                result_holder.append(result)

            app.run_worker(_show(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()

        assert result_holder[0].decision is TrustDecision.DENY


# ---------------------------------------------------------------------------
# Pilot: TextualPromptTrustGate integration
# ---------------------------------------------------------------------------


class TestTextualPromptTrustGate:
    @pytest.mark.asyncio
    async def test_trust_project_writes_allowlist(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        store_path = tmp_path / "trusted.toml"
        store = AllowlistStore(store_path)
        project = tmp_path / "myrepo"
        project.mkdir()
        conv = project / "AGENTS.md"
        conv.write_text("# conventions\n\n- write tests\n", encoding="utf-8")

        app = _app_for(companion_session)
        gate = TextualPromptTrustGate(app=app, store=store)
        decisions: list[TrustDecision] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _run() -> None:
                decisions.append(await gate.check(project, [conv]))

            app.run_worker(_run(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()

        assert decisions == [TrustDecision.ALLOW]
        assert store.contains(project) is True

    @pytest.mark.asyncio
    async def test_cached_decision_not_reprompted(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        store = AllowlistStore(tmp_path / "trusted.toml")
        project = tmp_path / "myrepo"
        project.mkdir()
        conv = project / "AGENTS.md"
        conv.write_text("hi", encoding="utf-8")

        app = _app_for(companion_session)
        gate = TextualPromptTrustGate(app=app, store=store)
        decisions: list[TrustDecision] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _run_pair() -> None:
                decisions.append(await gate.check(project, [conv]))
                decisions.append(await gate.check(project, [conv]))

            app.run_worker(_run_pair(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            # Answer the first modal; the second call must NOT push one.
            await pilot.press("o")
            await pilot.pause()
            await pilot.pause()

        assert decisions == [TrustDecision.ALLOW, TrustDecision.ALLOW]
        # "Trust once" does not persist.
        assert store.contains(project) is False

    @pytest.mark.asyncio
    async def test_pre_trusted_project_skips_modal(
        self, companion_session: Session, tmp_path: Path
    ) -> None:
        """A project already in the allowlist bypasses the modal."""
        store = AllowlistStore(tmp_path / "trusted.toml")
        project = tmp_path / "myrepo"
        project.mkdir()
        store.add(project, now=datetime(2026, 4, 24, tzinfo=UTC))

        app = _app_for(companion_session)
        gate = TextualPromptTrustGate(app=app, store=store)
        decisions: list[TrustDecision] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _run() -> None:
                decisions.append(await gate.check(project, [project / "AGENTS.md"]))

            app.run_worker(_run(), exclusive=False, exit_on_error=False)
            # No keypress — if a modal were pushed the worker would block.
            await pilot.pause()
            await pilot.pause()

        assert decisions == [TrustDecision.ALLOW]

    @pytest.mark.asyncio
    async def test_deny_caches_deny(self, companion_session: Session, tmp_path: Path) -> None:
        store = AllowlistStore(tmp_path / "trusted.toml")
        project = tmp_path / "myrepo"
        project.mkdir()
        conv = project / "AGENTS.md"
        conv.write_text("hi", encoding="utf-8")

        app = _app_for(companion_session)
        gate = TextualPromptTrustGate(app=app, store=store)
        decisions: list[TrustDecision] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            async def _run_pair() -> None:
                decisions.append(await gate.check(project, [conv]))
                decisions.append(await gate.check(project, [conv]))

            app.run_worker(_run_pair(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.pause()

        assert decisions == [TrustDecision.DENY, TrustDecision.DENY]
        assert store.contains(project) is False
