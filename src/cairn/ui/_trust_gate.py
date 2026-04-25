"""The real `TrustGate` for `trust_policy="prompt"`.

Pushes `TrustPromptModal`, awaits the user's three-way choice,
and caches the decision per-project for the rest of the process.
Persists "trust project" outcomes to the `AllowlistStore` so the
choice survives process restarts.

The modal lists filenames only — content preview was removed: it
risked pasting inflammatory or sensitive snippets into the trust
flow, and the project path + filenames are enough signal for the
"do I know this project?" decision.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cairn.conventions._trust import TrustDecision
from cairn.ui._screens._trust_prompt import TrustPromptModal

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.conventions._trust import AllowlistStore
    from cairn.ui._app import CairnApp


log = logging.getLogger(__name__)


class TextualPromptTrustGate:
    """`TrustGate` implementation backed by `TrustPromptModal`.

    The loader calls `check(project_root, files)` once per session.
    The first call per-project pushes a modal and records the
    outcome; subsequent calls return the cached decision without
    prompting. "Trust project" outcomes also write to the
    `AllowlistStore` so a future process defaults to ALLOW.
    """

    def __init__(self, *, app: CairnApp, store: AllowlistStore) -> None:
        self._app = app
        self._store = store
        self._cache: dict[Path, TrustDecision] = {}

    async def check(
        self,
        project_root: Path,
        files: list[Path],
    ) -> TrustDecision:
        resolved = project_root.resolve()
        cached = self._cache.get(resolved)
        if cached is not None:
            return cached
        if self._store.contains(resolved):
            # Persisted from a previous process — treat as pre-approved.
            self._cache[resolved] = TrustDecision.ALLOW
            return TrustDecision.ALLOW

        modal = TrustPromptModal(project_root=resolved, files=files)
        result = await self._app.push_screen_wait(modal)

        self._cache[resolved] = result.decision
        if result.decision is TrustDecision.ALLOW and result.persist:
            self._store.add(resolved)
            log.info("convention files trusted for %s (persisted)", resolved)
        elif result.decision is TrustDecision.ALLOW:
            log.info("convention files trusted for %s (this session only)", resolved)
        else:
            log.info("convention files denied for %s", resolved)
        return result.decision
