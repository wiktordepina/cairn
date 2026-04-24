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
    preview_file_lines,
)
from cairn.ui._trust_gate import TextualPromptTrustGate

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


# ---------------------------------------------------------------------------
# Unit: preview_file_lines
# ---------------------------------------------------------------------------


class TestPreviewFileLines:
    def test_short_file_not_truncated(self) -> None:
        body, truncated = preview_file_lines("line1\nline2\nline3")
        assert body == "line1\nline2\nline3"
        assert truncated is False

    def test_long_file_truncated_at_20_lines(self) -> None:
        text = "\n".join(f"line{i}" for i in range(1, 31))
        body, truncated = preview_file_lines(text)
        assert truncated is True
        # First 20 lines only.
        assert body.splitlines() == [f"line{i}" for i in range(1, 21)]

    def test_exact_boundary_not_truncated(self) -> None:
        text = "\n".join(f"line{i}" for i in range(1, 21))
        body, truncated = preview_file_lines(text)
        assert truncated is False
        assert body.splitlines() == [f"line{i}" for i in range(1, 21)]

    def test_custom_max_lines(self) -> None:
        text = "a\nb\nc\nd"
        body, truncated = preview_file_lines(text, max_lines=2)
        assert truncated is True
        assert body == "a\nb"


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
                        preview_body="hello",
                        preview_truncated=False,
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
                        preview_body="hello",
                        preview_truncated=False,
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
                        preview_body="hello",
                        preview_truncated=False,
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
                        preview_body="hello",
                        preview_truncated=False,
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
