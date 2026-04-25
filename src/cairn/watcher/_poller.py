"""`FileWatcher` — polling daemon that detects drift on watched files.

Polling cadence is fixed by `WatcherConfig.poll_interval_s`. Each
tick walks the watch set, compares `os.stat().st_mtime_ns` against
the snapshot, and re-hashes only when mtime drifted. A real content
change (mtime + hash both differ) is added to a per-process `dirty`
set. On the rising edge of drift — `dirty` was empty, now non-empty
— a `ConfigDriftDetected` event is dispatched to the observer chain.

Drift detected during an active turn is buffered: the rising-edge
emission is gated on `not is_turn_active()` so the banner doesn't
appear above a streaming assistant message. The next tick after the
turn completes drains the buffer.

After `/reload` succeeds, the caller invokes `resnapshot(new_set)`
which clears `dirty` and replaces the baseline. After `/reload`
fails (validation error), `dirty` stays populated — the banner
persists until the user fixes the config and reloads again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cairn.domain._events import ConfigDriftDetected, DriftChange
from cairn.watcher._snapshot import WatchEntry, WatchSet, snapshot_path

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from cairn.orchestrator._middleware import UIEventObserver


log = logging.getLogger(__name__)


_MIN_POLL_INTERVAL_S = 0.5
_DEFAULT_POLL_INTERVAL_S = 3.0


class FileWatcher:
    """Background coroutine that polls a `WatchSet` for drift."""

    def __init__(
        self,
        *,
        watch_set: WatchSet,
        observers: Sequence[UIEventObserver],
        is_turn_active: Callable[[], bool] = lambda: False,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if poll_interval_s < _MIN_POLL_INTERVAL_S:
            msg = f"poll_interval_s must be >= {_MIN_POLL_INTERVAL_S} (got {poll_interval_s})"
            raise ValueError(msg)
        self._watch_set = watch_set
        self._observers = tuple(observers)
        self._is_turn_active = is_turn_active
        self._poll_interval_s = poll_interval_s
        self._clock = clock or (lambda: datetime.now(UTC))

        self._dirty: dict[str, DriftChange] = {}
        """Per-path key → drift change since last reset. Drains on
        successful resnapshot."""
        self._announced = False
        """True once a `ConfigDriftDetected` has been emitted for the
        current `dirty` window. Reset by `resnapshot()`."""
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    # -- Public API -----------------------------------------------------

    @property
    def watch_set(self) -> WatchSet:
        """Currently active baseline."""
        return self._watch_set

    @property
    def has_drift(self) -> bool:
        """True if any drift has been detected since last resnapshot."""
        return bool(self._dirty)

    def add_observer(self, observer: UIEventObserver) -> None:
        """Append an observer to the dispatch chain.

        Lets the bootstrap attach the `TextualUIEventObserver` after
        the app exists (the watcher is constructed first so the
        Reloader can take a reference to it).
        """
        self._observers = (*self._observers, observer)

    async def start(self) -> None:
        """Launch the background polling task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="file-watcher")

    async def stop(self) -> None:
        """Signal shutdown and wait for the worker to exit."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except TimeoutError:
                log.warning("file watcher did not stop cleanly; cancelling")
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None

    async def tick(self) -> None:
        """Run one poll cycle. Public for tests; the loop calls it."""
        async with self._lock:
            await asyncio.to_thread(self._poll_once)
            self._maybe_announce()

    def resnapshot(self, new_set: WatchSet) -> None:
        """Replace the baseline; clear dirty + announced flags.

        Called by `/reload` once the new config has been validated and
        applied. After this call the next tick compares against
        *new_set*, not the previous baseline.
        """
        self._watch_set = new_set
        self._dirty.clear()
        self._announced = False

    # -- Worker loop ----------------------------------------------------

    async def _run(self) -> None:
        """Polling loop. Runs until `stop()` is called."""
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("file watcher tick failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_s,
                )
            except TimeoutError:
                continue

    def _poll_once(self) -> None:
        """Single synchronous poll — runs in a worker thread to keep
        the event loop free of `os.stat` + hashing overhead.

        Updates `self._dirty` in-place; never emits events directly.
        Emission decision happens in `_maybe_announce` on the loop.
        """
        for entry in self._watch_set.entries:
            try:
                stat = entry.path.stat()
            except FileNotFoundError:
                self._record_change(entry, kind="removed")
                continue
            except OSError:
                log.debug("watcher stat failed for %s", entry.path, exc_info=True)
                self._record_change(entry, kind="removed")
                continue

            if stat.st_mtime_ns == entry.mtime_ns:
                continue  # cheap path: nothing changed.

            # mtime drifted — confirm with a hash compare.
            new_snap = snapshot_path(entry.category, entry.path)
            if new_snap is None:
                self._record_change(entry, kind="removed")
                continue
            if new_snap.sha256 == entry.sha256:
                # touch-without-content-change. Update stored mtime
                # silently; no drift announced.
                self._update_baseline_entry(entry, new_snap)
                continue

            self._record_change(entry, kind="modified", new=new_snap)
            self._update_baseline_entry(entry, new_snap)

    def _maybe_announce(self) -> None:
        """Emit `ConfigDriftDetected` if drift exists, hasn't been
        announced for this window, and no turn is currently active.
        """
        if not self._dirty:
            return
        if self._announced:
            return
        if self._is_turn_active():
            return  # buffered; emit on the next tick after the turn completes.

        event = ConfigDriftDetected(
            detected_at=self._clock(),
            changes=tuple(self._dirty.values()),
        )
        self._announced = True
        for observer in self._observers:
            try:
                observer.observe(event)
            except Exception:  # noqa: BLE001
                log.exception("observer raised on ConfigDriftDetected")

    def _record_change(
        self,
        entry: WatchEntry,
        *,
        kind: str,
        new: WatchEntry | None = None,
    ) -> None:
        # Already-recorded paths keep their first-seen kind; subsequent
        # changes don't downgrade "removed" to "modified" within one
        # window. This is fine — the drift event is informational and
        # the user reloads to resolve it.
        if str(entry.path) in self._dirty:
            return
        self._dirty[str(entry.path)] = DriftChange(
            category=entry.category,
            path=entry.path,
            kind=kind,  # type: ignore[arg-type]
        )
        log.debug(
            "watcher drift: %s (%s) — kind=%s",
            entry.path,
            entry.category,
            kind,
        )
        del new  # reserved for future use; kept in signature for clarity.

    def _update_baseline_entry(self, old: WatchEntry, new: WatchEntry) -> None:
        """Replace one entry's snapshot fields without rebuilding the
        full `WatchSet` tuple ordering.
        """
        updated: list[WatchEntry] = []
        replaced = False
        for entry in self._watch_set.entries:
            if entry.path == old.path and not replaced:
                updated.append(
                    replace(
                        entry,
                        mtime_ns=new.mtime_ns,
                        sha256=new.sha256,
                        size=new.size,
                    ),
                )
                replaced = True
            else:
                updated.append(entry)
        if replaced:
            self._watch_set = WatchSet(entries=tuple(updated))
