"""`Reloader` — surgical config reload driven by `/reload`.

The reloader rebuilds the disk-derived collaborators of the running
orchestrator (`SecretResolver`, `ModelRegistry`, `ProviderRegistry`)
from a freshly-loaded `CairnConfig`, invalidates the convention and
profile-doc loader caches, and re-snapshots the file watcher's
baseline. The orchestrator instance, the active session, persistence,
the extraction worker, and the Textual app are all preserved.

If `load_config` raises `ConfigError` (validation failure), the
reloader returns a result with `ok=False` and does not touch any
collaborator — the user keeps the previously-good config and the
watcher's drift banner stays up until they fix the file and reload
again.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cairn.config import (
    ConfigError,
    ModelRegistry,
    SecretResolver,
    load_config,
)
from cairn.providers._registry import ProviderRegistry
from cairn.watcher._snapshot import build_watch_set, discover_watch_paths

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from cairn.conventions import ConventionLoader
    from cairn.memory import ProfileDocLoader
    from cairn.orchestrator._orchestrator import Orchestrator
    from cairn.watcher._poller import FileWatcher
    from cairn.watcher._snapshot import WatchCategory


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReloadResult:
    """Outcome of a single `Reloader.reload()` call."""

    ok: bool
    summary: str
    error: str | None = None
    primary_model_drift: tuple[str, str] | None = None
    """When the active session was created with a different model id
    than the new config's `primary_model` resolves to, this carries
    `(active_session_model_id, new_resolved_model_id)`. The active
    session is deliberately *not* rebound — see ADR 0042 — so the UI
    surfaces a hint banner pointing at `/new` (when implemented) or
    a restart."""


class Reloader:
    """Drives the surgical-reload sequence behind `/reload`."""

    def __init__(
        self,
        *,
        profile_name: str | None,
        orchestrator: Orchestrator,
        convention_loader: ConventionLoader,
        profile_doc_loader: ProfileDocLoader,
        file_watcher: FileWatcher,
        project_dir: Path | None = None,
        active_session_model: Callable[[], str | None] | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._orchestrator = orchestrator
        self._convention_loader = convention_loader
        self._profile_doc_loader = profile_doc_loader
        self._file_watcher = file_watcher
        self._project_dir = project_dir
        self._active_session_model = active_session_model
        self._lock = asyncio.Lock()

    async def reload(self) -> ReloadResult:
        """Re-load config, swap collaborators, invalidate caches,
        re-snapshot the watcher.

        Returns `ReloadResult(ok=True, summary=...)` on success or
        `ReloadResult(ok=False, error=...)` on validation failure. Two
        concurrent calls serialise via an internal lock; the second
        sees the new baseline and reports an empty summary.
        """
        async with self._lock:
            try:
                new_config = await asyncio.to_thread(
                    load_config,
                    profile=self._profile_name,
                    project_dir=self._project_dir,
                )
            except ConfigError as exc:
                log.warning("reload: config validation failed: %s", exc)
                return ReloadResult(ok=False, summary="", error=str(exc))
            except Exception as exc:  # noqa: BLE001 — defensive
                log.exception("reload: unexpected failure loading config")
                return ReloadResult(
                    ok=False,
                    summary="",
                    error=f"unexpected: {exc}",
                )

            new_secret_resolver = SecretResolver()
            new_model_registry = ModelRegistry(new_config.models)
            new_provider_registry = ProviderRegistry(
                config=new_config,
                secret_resolver=new_secret_resolver,
            )

            self._orchestrator.replace_collaborators(
                provider_registry=new_provider_registry,
                model_registry=new_model_registry,
            )
            self._convention_loader.invalidate()
            self._profile_doc_loader.invalidate()

            new_paths = discover_watch_paths(
                convention_files_config=new_config.active.convention_files,
                convention_loader=self._convention_loader,
                profile_doc_loader=self._profile_doc_loader,
                project_dir=self._project_dir,
            )
            self._file_watcher.resnapshot(build_watch_set(new_paths))

            primary_model_drift = self._detect_primary_model_drift(
                new_config_active_primary_model=new_config.active.primary_model,
                new_model_registry=new_model_registry,
            )

            summary = self._summarise(new_paths)
            log.info("reload: succeeded — %s", summary)
            return ReloadResult(
                ok=True,
                summary=summary,
                primary_model_drift=primary_model_drift,
            )

    def _detect_primary_model_drift(
        self,
        *,
        new_config_active_primary_model: str,
        new_model_registry: ModelRegistry,
    ) -> tuple[str, str] | None:
        """Return `(active_id, new_id)` if the active session's model
        differs from where the profile's `primary_model` ref now
        resolves; otherwise `None`.

        We deliberately don't rebind the session — see ADR 0042.
        Cross-provider history (tool-call id schemes, thinking blocks,
        cache shapes) makes mid-session model swaps a separate
        feature gated on a future explicit `/model` command.
        """
        if self._active_session_model is None:
            return None
        active_model = self._active_session_model()
        if active_model is None:
            return None
        try:
            new_resolved = new_model_registry.resolve(new_config_active_primary_model)
        except Exception:  # noqa: BLE001 — defensive, ref may be invalid
            log.exception(
                "reload: failed to resolve new primary_model %r against new registry",
                new_config_active_primary_model,
            )
            return None
        if new_resolved.id == active_model:
            return None
        return (active_model, new_resolved.id)

    @staticmethod
    def _summarise(rows: Sequence[tuple[WatchCategory | str, Path]]) -> str:
        """Format the reload-success banner copy.

        Category-count granularity per the design doc — no per-path
        enumeration.
        """
        counts: dict[str, int] = {}
        for category, _ in rows:
            counts[category] = counts.get(category, 0) + 1

        labels = {
            "config": "config layer",
            "convention": "convention file",
            "profile_doc": "profile doc",
        }
        parts: list[str] = []
        for category in ("config", "convention", "profile_doc"):
            n = counts.get(category, 0)
            if n == 0:
                continue
            label = labels[category]
            if n != 1:
                label = label + "s"
            parts.append(f"{n} {label}")
        if not parts:
            return "no watched files"
        return "Reloaded: " + ", ".join(parts) + "."
