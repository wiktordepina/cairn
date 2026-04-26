"""Pilot tests for the `/reload` slash command."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._widgets import Banner, ChatLog, CommandBar
from cairn.watcher import ReloadResult

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_with_reloader(session: Session, reloader: object) -> CairnApp:
    return CairnApp(
        orchestrator=cast("Orchestrator", Mock()),
        session=session,
        reloader=reloader,  # type: ignore[arg-type]
    )


class _SuccessReloader:
    def __init__(self) -> None:
        self.calls = 0

    async def reload(self) -> ReloadResult:
        self.calls += 1
        return ReloadResult(ok=True, summary="Reloaded: 1 config layer.")


class _FailingReloader:
    async def reload(self) -> ReloadResult:
        return ReloadResult(ok=False, summary="", error="schema_version missing")


class _RolePinDriftReloader:
    async def reload(self) -> ReloadResult:
        return ReloadResult(
            ok=True,
            summary="Reloaded: 1 config layer.",
            primary_model_drift=("opus-4-7", "haiku-4-5"),
        )


class TestReloadCommand:
    @pytest.mark.asyncio
    async def test_reload_success_renders_banner(self, companion_session: Session) -> None:
        reloader = _SuccessReloader()
        app = _app_with_reloader(companion_session, reloader)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "Reloaded: 1 config layer." in rendered
            assert reloader.calls == 1

    @pytest.mark.asyncio
    async def test_reload_failure_renders_warning(self, companion_session: Session) -> None:
        app = _app_with_reloader(companion_session, _FailingReloader())
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "reload failed" in rendered
            assert "schema_version missing" in rendered
            warning = [b for b in banners if b.kind == "warning"]
            assert len(warning) == 1

    @pytest.mark.asyncio
    async def test_reload_without_reloader_shows_placeholder(
        self, companion_session: Session
    ) -> None:
        app = CairnApp(
            orchestrator=cast("Orchestrator", Mock()),
            session=companion_session,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            assert any(
                "bootstrap did not wire a file watcher" in str(b.renderable) for b in banners
            )

    @pytest.mark.asyncio
    async def test_reload_role_pin_drift_reverts_session_model(
        self, companion_session: Session
    ) -> None:
        # The drift path now actively reverts the session row's model
        # to whatever config resolves to (ADR 0045) — rather than
        # surfacing a "restart to switch" hint. Stub orchestrator
        # records the swap and returns a refreshed session.
        from cairn.domain._events import ModelSwapped

        swapped: list[ModelSwapped] = []
        refreshed_session = companion_session.model_copy(update={"model": "haiku-4-5"})

        class _Orch:
            async def swap_session_model(
                self, session_id: str, new_model_id: str, *, mode: str
            ) -> Session:
                swapped.append(
                    ModelSwapped(
                        session_id=session_id,
                        from_model=companion_session.model,
                        to_model=new_model_id,
                        mode=mode,  # type: ignore[arg-type]
                    )
                )
                return refreshed_session

        app = CairnApp(
            orchestrator=cast("Orchestrator", _Orch()),
            session=companion_session,
            reloader=_RolePinDriftReloader(),  # type: ignore[arg-type]
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "Reloaded: 1 config layer." in rendered
            assert "session model reverted to config" in rendered
            # No registry wired here, so the banner falls back to ids.
            assert "was opus-4-7, now haiku-4-5" in rendered
            assert len(swapped) == 1
            assert swapped[0].mode == "revert"
            assert swapped[0].to_model == "haiku-4-5"

    @pytest.mark.asyncio
    async def test_reload_revert_banner_uses_display_names(
        self, companion_session: Session
    ) -> None:
        """When the registry is wired, the revert banner shows display names."""
        from cairn.config._models import ModelConfig
        from cairn.config._registry import ModelRegistry

        def _m(model_id: str, display_name: str) -> ModelConfig:
            return ModelConfig(
                id=model_id,
                provider="anthropic",
                display_name=display_name,
                context_window=200_000,
                max_output_tokens=8_000,
                supports_tools=True,
                input_cost_per_1m=1.0,
                output_cost_per_1m=5.0,
            )

        registry = ModelRegistry(
            [_m("opus-4-7", "Claude Opus 4.7"), _m("haiku-4-5", "Claude Haiku 4.5")]
        )
        refreshed_session = companion_session.model_copy(update={"model": "haiku-4-5"})

        class _Orch:
            async def swap_session_model(
                self, session_id: str, new_model_id: str, *, mode: str
            ) -> Session:
                return refreshed_session

        app = CairnApp(
            orchestrator=cast("Orchestrator", _Orch()),
            session=companion_session,
            reloader=_RolePinDriftReloader(),  # type: ignore[arg-type]
            model_registry=registry,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "was Claude Opus 4.7, now Claude Haiku 4.5" in rendered

    @pytest.mark.asyncio
    async def test_reload_mid_turn_queues(self, companion_session: Session) -> None:
        reloader = _SuccessReloader()
        app = _app_with_reloader(companion_session, reloader)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            # Pretend a turn is in flight.
            from cairn.ui._widgets import ActivityIndicator

            screen.query_one(ActivityIndicator).set_streaming()
            assert screen.is_turn_active() is True

            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            # Reload was NOT executed yet.
            assert reloader.calls == 0
            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "reload queued" in rendered

            # Simulate turn completion — pending reload flushes.
            screen.query_one(ActivityIndicator).set_idle()
            screen._flush_pending_reload()  # noqa: SLF001
            await pilot.pause()
            await pilot.pause()
            assert reloader.calls == 1
