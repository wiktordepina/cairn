"""Trust gate — decides whether to load project convention files.

Project files live in the repo, which may be a clone with arbitrary
content. The trust gate makes first-encounter decisions per-project.

Three implementations cover the V1 `trust_policy` values:

- `AlwaysTrustGate` — always ALLOW. Convenient, risky; appropriate
  when you only ever work in your own repos.
- `AllowlistTrustGate` — ALLOW iff the project root is in the
  allowlist or is a descendant of one. Backed by an `AllowlistStore`
  reading `$XDG_CONFIG_HOME/cairn/trusted_projects.toml`.
- `DenyingPromptTrustGate` — placeholder for `trust_policy="prompt"`
  until the UI brick lands. DENYs every call with a WARNING (once
  per project per process), so users who configured `prompt` notice
  the degradation rather than silently losing their conventions.

User-level files are not subject to the trust gate — they live
under the user's own config dir, same trust boundary as soul doc /
user-context / MEMORY.md.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

log = logging.getLogger(__name__)


DEFAULT_TRUSTED_PROJECTS_FILENAME = "trusted_projects.toml"


class TrustDecision(StrEnum):
    """Binary trust decision."""

    ALLOW = "allow"
    DENY = "deny"


@runtime_checkable
class TrustGate(Protocol):
    """Gate deciding whether project convention files may be loaded."""

    async def check(
        self,
        project_root: Path,
        files: list[Path],
    ) -> TrustDecision:
        """Return ALLOW if *files* may be loaded from *project_root*."""
        ...


# ---------------------------------------------------------------------------
# Allowlist storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustedProject:
    """One entry in the allowlist."""

    path: Path
    added_at: datetime


def default_allowlist_path() -> Path:
    """Return the default path for the trusted-projects TOML file.

    Resolves `$XDG_CONFIG_HOME/cairn/trusted_projects.toml`, falling
    back to `~/.config/cairn/trusted_projects.toml` when XDG is unset.
    Trust state is user-level, not per-profile — one file per user.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "cairn" / DEFAULT_TRUSTED_PROJECTS_FILENAME


class AllowlistStore:
    """TOML-backed store for the project allowlist.

    Single flat list of `[[projects]]` entries:

    ```toml
    [[projects]]
    path = "/home/user/projects/cairn"
    added_at = "2026-04-23T10:30:00Z"
    ```

    Malformed files are backed up to `<path>.broken` on first read
    and treated as empty; a fresh write then creates a clean file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def contains(self, project_root: Path) -> bool:
        """True iff *project_root* is in the allowlist or a descendant."""
        target = project_root.resolve()
        for entry in self.list_trusted():
            try:
                target.relative_to(entry.path)
            except ValueError:
                continue
            return True
        return False

    def add(self, project_root: Path, *, now: datetime | None = None) -> None:
        """Append *project_root* to the allowlist (no-op if already exact).

        Parent directory is created with mode 0o700. Write is atomic
        via `tmp + rename`.
        """
        resolved = project_root.resolve()
        now_dt = now or datetime.now(tz=UTC)
        entries = list(self.list_trusted())
        if any(e.path == resolved for e in entries):
            return
        entries.append(TrustedProject(path=resolved, added_at=now_dt))
        self._write(entries)

    def remove(self, project_root: Path) -> bool:
        """Remove an exact match. Returns True if something was removed."""
        resolved = project_root.resolve()
        entries = list(self.list_trusted())
        kept = [e for e in entries if e.path != resolved]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True

    def list_trusted(self) -> list[TrustedProject]:
        """Return all entries. Missing or malformed files → empty list."""
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_bytes()
            parsed: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            log.warning(
                "trusted_projects file unreadable; backing up and treating as empty: %s",
                self._path,
            )
            self._back_up_broken()
            return []
        projects_raw = parsed.get("projects")
        if not isinstance(projects_raw, list):
            return []
        out: list[TrustedProject] = []
        for item in cast("list[Any]", projects_raw):
            if not isinstance(item, dict):
                continue
            entry = cast("dict[str, Any]", item)
            raw_path = entry.get("path")
            raw_at = entry.get("added_at")
            if not isinstance(raw_path, str):
                continue
            try:
                added_at = _parse_datetime(raw_at)
            except ValueError:
                continue
            out.append(TrustedProject(path=Path(raw_path), added_at=added_at))
        return out

    # ------------------------------------------------------------------

    def _write(self, entries: list[TrustedProject]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lines = ["# cairn trusted projects — managed file\n"]
        for entry in entries:
            lines.append("\n[[projects]]\n")
            lines.append(f'path = "{entry.path}"\n')
            lines.append(f'added_at = "{_format_datetime(entry.added_at)}"\n')
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        tmp.replace(self._path)

    def _back_up_broken(self) -> None:
        backup = self._path.with_suffix(self._path.suffix + ".broken")
        try:
            self._path.replace(backup)
        except OSError:
            log.exception("failed to back up broken allowlist to %s", backup)


def _parse_datetime(raw: object) -> datetime:
    """Parse an ISO-8601 timestamp, accepting the `Z` suffix."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if not isinstance(raw, str):
        raise ValueError
    value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _format_datetime(dt: datetime) -> str:
    """Format as ISO-8601 with a trailing Z for UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# TrustGate implementations
# ---------------------------------------------------------------------------


class AlwaysTrustGate:
    """Grant every project unconditionally.

    Warning: every cloned repo's `AGENTS.md` / `CLAUDE.md` / `CAIRN.md`
    will be injected into the model's system prompt. Use only on
    machines where you audit every repo you open.
    """

    async def check(
        self,
        project_root: Path,  # noqa: ARG002 — protocol signature
        files: list[Path],  # noqa: ARG002 — protocol signature
    ) -> TrustDecision:
        return TrustDecision.ALLOW


class AllowlistTrustGate:
    """ALLOW iff *project_root* is in the allowlist or a descendant."""

    def __init__(self, store: AllowlistStore) -> None:
        self._store = store

    async def check(
        self,
        project_root: Path,
        files: list[Path],  # noqa: ARG002 — protocol signature
    ) -> TrustDecision:
        if self._store.contains(project_root):
            return TrustDecision.ALLOW
        log.warning(
            "convention files not loaded: project not in allowlist (%s). "
            "Add an entry under [[projects]] in %s to trust it.",
            project_root,
            self._store.path,
        )
        return TrustDecision.DENY


class DenyingPromptTrustGate:
    """Placeholder for `trust_policy="prompt"` until the UI brick lands.

    Always returns DENY. Logs a WARNING the first time each project is
    encountered within the process, so users who configured `prompt`
    notice that their conventions aren't loading rather than silently
    losing them.
    """

    def __init__(self) -> None:
        self._warned: set[Path] = set()

    async def check(
        self,
        project_root: Path,
        files: list[Path],  # noqa: ARG002 — protocol signature
    ) -> TrustDecision:
        resolved = project_root.resolve()
        if resolved not in self._warned:
            self._warned.add(resolved)
            log.warning(
                "convention files not loaded for %s: trust_policy='prompt' "
                "requires the UI brick (not yet shipped). Switch to "
                "'always' or 'project_allowlist' in your profile config.",
                resolved,
            )
        return TrustDecision.DENY


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def trust_gate_for_policy(
    policy: Literal["prompt", "always", "project_allowlist"],
    *,
    allowlist_store: AllowlistStore | None = None,
) -> TrustGate:
    """Build the `TrustGate` matching *policy*.

    *allowlist_store* is required when *policy* is `"project_allowlist"`;
    a default is constructed otherwise.
    """
    if policy == "always":
        return AlwaysTrustGate()
    if policy == "project_allowlist":
        store = allowlist_store or AllowlistStore(default_allowlist_path())
        return AllowlistTrustGate(store)
    if policy == "prompt":
        return DenyingPromptTrustGate()
    msg = f"unknown trust_policy: {policy!r}"
    raise ValueError(msg)
