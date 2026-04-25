"""Tests for `cairn.watcher.FileWatcher` — polling daemon."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.domain._events import ConfigDriftDetected
from cairn.watcher import FileWatcher, build_watch_set

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cairn.domain._events import UIEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Recorder:
    """Sync observer that captures every UIEvent it sees."""

    def __init__(self) -> None:
        self.events: list[UIEvent] = []

    def observe(self, event: UIEvent) -> None:
        self.events.append(event)


def _make_watcher(
    paths: list[tuple[str, Path]],
    *,
    is_turn_active: Callable[[], bool] | None = None,
) -> tuple[FileWatcher, _Recorder]:
    watch_set = build_watch_set(paths)
    recorder = _Recorder()
    watcher = FileWatcher(
        watch_set=watch_set,
        observers=[recorder],
        is_turn_active=is_turn_active or (lambda: False),
        poll_interval_s=0.5,
        clock=lambda: datetime(2026, 4, 25, 21, 30, tzinfo=UTC),
    )
    return watcher, recorder


def _drift_events(recorder: _Recorder) -> list[ConfigDriftDetected]:
    return [ev for ev in recorder.events if isinstance(ev, ConfigDriftDetected)]


# ---------------------------------------------------------------------------
# tick — content unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_no_drift_when_files_unchanged(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"a")
    watcher, recorder = _make_watcher([("config", a)])

    await watcher.tick()

    assert _drift_events(recorder) == []
    assert not watcher.has_drift


# ---------------------------------------------------------------------------
# tick — touch-only (mtime bumped, content same)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_touch_only_does_not_drift(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"a")
    watcher, recorder = _make_watcher([("config", a)])
    original_mtime = watcher.watch_set.entries[0].mtime_ns

    # Bump mtime forwards without content change.
    bumped = original_mtime + 1_000_000_000  # +1 second in ns
    a.touch()
    import os

    os.utime(a, ns=(bumped, bumped))

    await watcher.tick()

    assert _drift_events(recorder) == []
    assert not watcher.has_drift
    # Stored mtime should now reflect the bump.
    assert watcher.watch_set.entries[0].mtime_ns >= bumped


# ---------------------------------------------------------------------------
# tick — real edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_real_edit_emits_drift_once(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"original")
    watcher, recorder = _make_watcher([("config", a)])

    a.write_bytes(b"changed")
    # Force mtime forwards in case the test's clock resolution coalesces.
    import os
    import time

    new_ns = time.time_ns() + 5_000_000_000
    os.utime(a, ns=(new_ns, new_ns))

    await watcher.tick()

    drifts = _drift_events(recorder)
    assert len(drifts) == 1
    assert drifts[0].changes[0].path == a
    assert drifts[0].changes[0].kind == "modified"
    assert drifts[0].changes[0].category == "config"

    # Subsequent ticks while still dirty must not re-emit.
    await watcher.tick()
    assert len(_drift_events(recorder)) == 1


@pytest.mark.asyncio
async def test_tick_two_edits_emit_one_event(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")
    watcher, recorder = _make_watcher([("config", a)])

    import os
    import time

    a.write_bytes(b"v2")
    os.utime(a, ns=(time.time_ns() + 5_000_000_000,) * 2)
    await watcher.tick()
    a.write_bytes(b"v3")
    os.utime(a, ns=(time.time_ns() + 10_000_000_000,) * 2)
    await watcher.tick()

    assert len(_drift_events(recorder)) == 1


# ---------------------------------------------------------------------------
# tick — file removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_file_deleted_emits_drift(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"a")
    watcher, recorder = _make_watcher([("config", a)])

    a.unlink()
    await watcher.tick()

    drifts = _drift_events(recorder)
    assert len(drifts) == 1
    assert drifts[0].changes[0].kind == "removed"
    assert drifts[0].changes[0].path == a


# ---------------------------------------------------------------------------
# tick — defer to turn completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_buffers_drift_during_turn(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")

    turn_active = True

    def is_active() -> bool:
        return turn_active

    watcher, recorder = _make_watcher(
        [("config", a)],
        is_turn_active=is_active,
    )

    import os
    import time

    a.write_bytes(b"v2")
    os.utime(a, ns=(time.time_ns() + 5_000_000_000,) * 2)
    await watcher.tick()

    # Turn still active — drift detected but no event.
    assert watcher.has_drift
    assert _drift_events(recorder) == []

    # Turn completes — next tick drains the buffer.
    turn_active = False
    await watcher.tick()
    assert len(_drift_events(recorder)) == 1


# ---------------------------------------------------------------------------
# resnapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resnapshot_clears_dirty_and_allows_new_drift(
    tmp_path: Path,
) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")
    watcher, recorder = _make_watcher([("config", a)])

    import os
    import time

    a.write_bytes(b"v2")
    os.utime(a, ns=(time.time_ns() + 5_000_000_000,) * 2)
    await watcher.tick()
    assert len(_drift_events(recorder)) == 1

    # /reload's surgical resnapshot clears state.
    new_set = build_watch_set([("config", a)])
    watcher.resnapshot(new_set)
    assert not watcher.has_drift

    # A subsequent edit produces a new event.
    a.write_bytes(b"v3")
    os.utime(a, ns=(time.time_ns() + 10_000_000_000,) * 2)
    await watcher.tick()
    assert len(_drift_events(recorder)) == 2


@pytest.mark.asyncio
async def test_failed_reload_keeps_dirty_set(tmp_path: Path) -> None:
    """Validation failure → /reload does NOT call resnapshot, so the
    watcher's dirty set persists and the banner stays up."""
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")
    watcher, recorder = _make_watcher([("config", a)])

    import os
    import time

    a.write_bytes(b"v2")
    os.utime(a, ns=(time.time_ns() + 5_000_000_000,) * 2)
    await watcher.tick()
    assert watcher.has_drift
    assert len(_drift_events(recorder)) == 1

    # Simulate /reload failing — caller never invokes resnapshot.
    # has_drift must remain True; a subsequent tick must not re-emit.
    await watcher.tick()
    assert watcher.has_drift
    assert len(_drift_events(recorder)) == 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_cycle(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")
    watcher, _recorder = _make_watcher([("config", a)])

    await watcher.start()
    # Yield once so the worker actually runs at least one tick.
    await asyncio.sleep(0.05)
    await watcher.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")
    watcher, _recorder = _make_watcher([("config", a)])

    await watcher.start()
    await watcher.start()  # second call must not raise or spawn a duplicate
    await watcher.stop()


def test_init_rejects_subsecond_intervals(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"x")
    watch_set = build_watch_set([("config", a)])

    with pytest.raises(ValueError, match="poll_interval_s"):
        FileWatcher(
            watch_set=watch_set,
            observers=[],
            poll_interval_s=0.1,
        )


# ---------------------------------------------------------------------------
# Observer error swallowing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observer_exception_does_not_break_watcher(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    a.write_bytes(b"v1")

    class _Boom:
        def observe(self, event: UIEvent) -> None:
            del event
            msg = "boom"
            raise RuntimeError(msg)

    watch_set = build_watch_set([("config", a)])
    watcher = FileWatcher(
        watch_set=watch_set,
        observers=[_Boom()],
        poll_interval_s=0.5,
    )

    import os
    import time

    a.write_bytes(b"v2")
    os.utime(a, ns=(time.time_ns() + 5_000_000_000,) * 2)
    # Must not propagate the observer's exception.
    await watcher.tick()
    assert watcher.has_drift
