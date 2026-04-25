"""Per-project prompt-history persistence.

A tiny JSONL store, keyed by ``(profile, project_root)``. Each line is
``{"prompt": "<text>"}``; the file is rewritten on every save so the
on-disk size stays bounded by ``max_size``. JSONL is overkill for the
current shape but leaves room to grow (timestamps, command vs. prompt
flags) without a migration.

The store is intentionally synchronous — prompt history is small (a
few hundred lines), writes happen at human-typing cadence, and a real
async path would need its own executor. The thin API matches what
`SessionScreen` actually calls: load on mount, save after every
submit.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


class PromptHistoryStore:
    """Load/save the prompt history list for one project."""

    def __init__(self, path: Path, *, max_size: int) -> None:
        self._path = path
        self._max_size = max(0, max_size)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def max_size(self) -> int:
        return self._max_size

    def load(self) -> list[str]:
        """Return the persisted history (oldest → newest), trimmed to cap.

        Missing file → empty list. Corrupt lines are skipped with a
        debug log; we never let prompt-history I/O break the UI.
        """
        if self._max_size == 0:
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            log.debug("prompt-history: failed to read %s", self._path, exc_info=True)
            return []

        history: list[str] = []
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                row: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            prompt = row.get("prompt") if "prompt" in row else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(prompt, str) and prompt:
                history.append(prompt)
        if len(history) > self._max_size:
            history = history[-self._max_size :]
        return history

    def save(self, history: list[str]) -> None:
        """Persist *history* (oldest → newest), trimmed to ``max_size``."""
        if self._max_size == 0:
            return
        trimmed = history[-self._max_size :] if len(history) > self._max_size else history
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as fh:
                for prompt in trimmed:
                    fh.write(json.dumps({"prompt": prompt}, ensure_ascii=False))
                    fh.write("\n")
        except OSError:
            log.debug("prompt-history: failed to write %s", self._path, exc_info=True)
