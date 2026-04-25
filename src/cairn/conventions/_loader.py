"""`ConventionLoader` — discover, gate, read, truncate, cache.

The orchestrator's `StandardContextManager` holds one instance per
session. On first `load()` the loader:

1. Runs `discover_user_files` + `discover_project_files`.
2. Passes project files through the `TrustGate`. A DENY means
   "no project files this session", but user-level files are still
   loaded (they come from the user's own config dir).
3. Reads each allowed file, skipping binaries. Applies the
   `max_bytes_per_file` cap with paragraph-boundary truncation.
4. Caches the list so subsequent turns don't re-hit disk. Call
   `invalidate()` to drop the cache (for `/reload` once the UI
   brick lands).

`enabled=False` short-circuits to an empty list with no disk I/O.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cairn.conventions._discovery import (
    ConventionSource,
    discover_project_files,
    discover_user_files,
)
from cairn.conventions._render import ConventionFile
from cairn.conventions._trust import TrustDecision

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.config import ConventionFilesConfig
    from cairn.conventions._trust import TrustGate


log = logging.getLogger(__name__)


_BINARY_PROBE_BYTES = 1024
"""Number of bytes read when probing for the null-byte binary signature."""

_TRUNCATION_SUFFIX_TEMPLATE = "\n\n<!-- truncated: original was {n} bytes -->"


class ConventionLoader:
    """Caches convention-file loading for one session."""

    def __init__(
        self,
        *,
        config: ConventionFilesConfig,
        trust_gate: TrustGate,
        cwd: Path,
    ) -> None:
        self._config = config
        self._trust_gate = trust_gate
        self._cwd = cwd
        self._cache: list[ConventionFile] | None = None

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def config(self) -> ConventionFilesConfig:
        return self._config

    async def load(self) -> list[ConventionFile]:
        """Return the convention files for this session, caching.

        First call runs discovery + trust check + read. Subsequent
        calls return the cached list.
        """
        if self._cache is not None:
            return self._cache
        if not self._config.enabled:
            self._cache = []
            return self._cache

        out: list[ConventionFile] = []

        # 1. User-level files (bypass the trust gate).
        for path, filename, source in discover_user_files(
            self._config.user_level_paths,
        ):
            loaded = _read_and_truncate(
                path,
                filename,
                source,
                max_bytes=self._config.max_bytes_per_file,
            )
            if loaded is not None:
                out.append(loaded)

        # 2. Project files (through the trust gate).
        project_discoveries = discover_project_files(self._cwd, self._config)
        if project_discoveries:
            project_paths = [p for p, _, _ in project_discoveries]
            decision = await self._trust_gate.check(self._cwd, project_paths)
            if decision is TrustDecision.ALLOW:
                for path, filename, source in project_discoveries:
                    loaded = _read_and_truncate(
                        path,
                        filename,
                        source,
                        max_bytes=self._config.max_bytes_per_file,
                    )
                    if loaded is not None:
                        out.append(loaded)

        self._cache = out
        return self._cache

    def invalidate(self) -> None:
        """Drop the cache — next `load()` re-runs discovery + I/O."""
        self._cache = None


# ---------------------------------------------------------------------------
# Read + truncate helpers
# ---------------------------------------------------------------------------


def _read_and_truncate(
    path: Path,
    filename: str,
    source: ConventionSource,
    *,
    max_bytes: int,
) -> ConventionFile | None:
    """Read *path*, applying the size cap with paragraph-boundary truncation.

    Returns `None` if the file can't be read, is binary, or decodes to
    an empty string.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        log.warning("convention file read failed: %s", path, exc_info=True)
        return None

    if _looks_binary(raw[:_BINARY_PROBE_BYTES]):
        log.warning("convention file looks binary; skipping: %s", path)
        return None

    original_size = len(raw)
    if original_size <= max_bytes:
        text = raw.decode("utf-8", errors="replace")
        truncated_from: int | None = None
    else:
        text = _truncate_at_paragraph(raw, max_bytes)
        text += _TRUNCATION_SUFFIX_TEMPLATE.format(n=original_size)
        truncated_from = original_size

    if not text.strip():
        return None

    return ConventionFile(
        filename=filename,
        path=path,
        content=text,
        truncated_from=truncated_from,
        source=source,
    )


def _truncate_at_paragraph(raw: bytes, max_bytes: int) -> str:
    """Return *raw*'s first `max_bytes` decoded, trimmed at a paragraph
    boundary if possible.

    Prefers `"\\n\\n"`, then `"\\n"`, then a hard byte cut as last resort.
    """
    head = raw[:max_bytes].decode("utf-8", errors="replace")
    paragraph_idx = head.rfind("\n\n")
    if paragraph_idx > 0:
        return head[:paragraph_idx]
    newline_idx = head.rfind("\n")
    if newline_idx > 0:
        return head[:newline_idx]
    return head


def _looks_binary(sample: bytes) -> bool:
    """True if *sample* contains a null byte — crude but sufficient."""
    return b"\x00" in sample
