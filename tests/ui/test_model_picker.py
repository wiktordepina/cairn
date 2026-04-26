"""Pilot tests for `ModelPickerModal`.

Verifies the picker renders every configured model, marks the
session's current model, and returns the picked id (or None on
abort).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from textual.widgets import Label

from cairn.config._models import ModelConfig
from cairn.ui._app import CairnApp
from cairn.ui._screens._model_picker import ModelPickerModal

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _model(id: str, **kw: Any) -> ModelConfig:
    base: dict[str, Any] = {
        "id": id,
        "provider": "anthropic",
        "display_name": id,
        "context_window": 200_000,
        "max_output_tokens": 8_000,
        "supports_tools": True,
        "input_cost_per_1m": 1.0,
        "output_cost_per_1m": 5.0,
    }
    base.update(kw)
    return ModelConfig(**base)


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


class TestModelPickerModal:
    @pytest.mark.asyncio
    async def test_renders_current_marker(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        models = (_model("opus-4-7"), _model("haiku-4-5"), _model("gpt-5"))
        async with app.run_test() as pilot:
            await pilot.pause()

            async def inner() -> None:
                await app.push_screen_wait(
                    ModelPickerModal(
                        models=models,
                        current_model_id="haiku-4-5",
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()

            modal = app.screen
            assert isinstance(modal, ModelPickerModal)
            header_labels = [str(label.renderable) for label in modal.query(Label)]
            # Header line names the current model unambiguously.
            assert any("current: haiku-4-5" in t for t in header_labels)
            # The active row carries the marker and is bolded via CSS class
            # (we check for the marker text since CSS isn't inspected).
            assert any("◀ current" in t for t in header_labels)

            await pilot.press("escape")
            await worker.wait()

    @pytest.mark.asyncio
    async def test_escape_returns_none(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        models = (_model("opus-4-7"),)
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[str | None] = []

            async def inner() -> None:
                holder.append(
                    await app.push_screen_wait(
                        ModelPickerModal(
                            models=models,
                            current_model_id="opus-4-7",
                        )
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            await pilot.press("escape")
            await worker.wait()

            assert holder == [None]

    @pytest.mark.asyncio
    async def test_enter_dismisses_with_focused_model_id(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        models = (_model("opus-4-7"), _model("haiku-4-5"))
        async with app.run_test() as pilot:
            await pilot.pause()
            holder: list[str | None] = []

            async def inner() -> None:
                holder.append(
                    await app.push_screen_wait(
                        ModelPickerModal(
                            models=models,
                            current_model_id="opus-4-7",
                        )
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()
            # Initial focus lands on the current model row.
            await pilot.press("enter")
            await worker.wait()

            assert holder == ["opus-4-7"]

    def test_rejects_empty_model_list(self) -> None:
        with pytest.raises(ValueError, match="at least one model"):
            ModelPickerModal(models=(), current_model_id="x")

    @pytest.mark.asyncio
    async def test_rows_render_display_name_then_id(self, companion_session: Session) -> None:
        """Picker rows show ``<display_name> | <id>``.

        The id stays visible (separated by a pipe rather than parens,
        because ``display_name`` may itself contain parens — e.g.
        ``"DeepSeek V3.1 (OpenRouter)"``).
        """
        app = _app_for(companion_session)
        models = (
            _model("deepseek-v4-pro", display_name="DeepSeek V4 Pro"),
            _model("deepseek-v4-flash", display_name="DeepSeek V4 Flash"),
        )
        async with app.run_test() as pilot:
            await pilot.pause()

            async def inner() -> None:
                await app.push_screen_wait(
                    ModelPickerModal(
                        models=models,
                        current_model_id="deepseek-v4-pro",
                    )
                )

            worker = app.run_worker(inner(), exclusive=False, exit_on_error=False)
            await pilot.pause()

            modal = app.screen
            assert isinstance(modal, ModelPickerModal)
            text = "\n".join(str(label.renderable) for label in modal.query(Label))
            assert "current: DeepSeek V4 Pro" in text
            assert "DeepSeek V4 Pro | deepseek-v4-pro" in text
            assert "DeepSeek V4 Flash | deepseek-v4-flash" in text

            await pilot.press("escape")
            await worker.wait()
