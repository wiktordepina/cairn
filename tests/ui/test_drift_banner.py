"""Pilot: ConfigDriftDetected lands as a banner on the session screen."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain._events import ConfigDriftDetected, DriftChange
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._widgets import Banner, ChatLog

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _drift_event() -> ConfigDriftDetected:
    return ConfigDriftDetected(
        detected_at=datetime(2026, 4, 25, 21, 30, tzinfo=UTC),
        changes=(
            DriftChange(category="config", path=Path("/x/config.toml"), kind="modified"),
            DriftChange(category="convention", path=Path("/x/AGENTS.md"), kind="modified"),
        ),
    )


class TestDriftBanner:
    @pytest.mark.asyncio
    async def test_drift_event_renders_banner(self, companion_session: Session) -> None:
        app = CairnApp(orchestrator=cast("Orchestrator", Mock()), session=companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            observer = TextualUIEventObserver(app=app)
            observer.observe(_drift_event())
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "on-disk changes detected" in rendered
            assert "1 config layer" in rendered
            assert "1 convention file" in rendered
            assert "/reload" in rendered
