"""Context assembly — system-prompt builder for companion sessions.

`StandardContextManager` implements the orchestrator's `ContextManager`
protocol. Every turn it assembles a tagged system prompt from:

- `<identity>` — soul document (bundled fallback if the configured file
  is missing).
- `<user_context>` — free-form user context doc.
- `<memory_index>` — MEMORY.md, loaded verbatim as an index of
  `[title](memories/<slug>.md) — hook` lines. Per-memory bodies under
  `memories/` are NOT loaded in V1; they surface only through the
  `/memory` command once the UI brick lands.
- `<retrieved_memories>` — observations pulled by `MemoryService` for
  this turn, rendered as bullet points with type + importance.
- `<persona_system_prompt>` — any existing persona prompt, appended as
  the final section.

Files are read lazily on first use and cached for the lifetime of the
manager. A defensive byte cap trims documents that have grown too large
to fit the system prompt comfortably.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cairn.conventions import ConventionFile, render_conventions
from cairn.domain._provider import ProviderRequest, SystemPromptSegment

if TYPE_CHECKING:
    from pathlib import Path

    from cairn.conventions import ConventionLoader
    from cairn.domain._memory import MemoryEntry
    from cairn.domain._messages import Message
    from cairn.domain._provider import ToolDefinition
    from cairn.domain._sessions import Session


log = logging.getLogger(__name__)


DEFAULT_MAX_FILE_BYTES = 20 * 1024  # 20 KB
"""Defensive cap on each profile doc. Anything past this limit is
truncated with a WARNING so the system prompt stays bounded."""


# Minimal built-in soul document, used when the configured file is
# missing. Intentionally tiny — a real deployment will always supply
# its own.
_BUILTIN_SOUL_DOCUMENT = (
    "You are a coding companion. Be empathetic, assertive, and logical. "
    "When you don't know something, say so. Keep responses concise."
)


class ProfileDocLoader:
    """Read-and-cache helper for the three tier-3 profile docs.

    Each `load_*` method returns the file's contents (UTF-8), or an
    empty string if the file doesn't exist. Results are cached — the
    first call pays the disk read, subsequent calls return the cached
    string. Call `invalidate()` to drop the cache (used by `/reload`
    once the UI brick lands).
    """

    def __init__(
        self,
        *,
        soul_document_path: Path,
        user_context_path: Path,
        memory_md_path: Path,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        builtin_soul_document: str = _BUILTIN_SOUL_DOCUMENT,
    ) -> None:
        self._soul_path = soul_document_path
        self._user_context_path = user_context_path
        self._memory_md_path = memory_md_path
        self._max_bytes = max_file_bytes
        self._builtin_soul = builtin_soul_document
        self._cache: dict[str, str] = {}

    def load_soul_document(self) -> str:
        """Return the soul doc, falling back to the bundled default."""
        cached = self._cache.get("soul")
        if cached is not None:
            return cached
        text = self._read(self._soul_path)
        if not text:
            text = self._builtin_soul
        self._cache["soul"] = text
        return text

    def load_user_context(self) -> str:
        """Return the user-context doc, or an empty string if missing."""
        cached = self._cache.get("user_context")
        if cached is not None:
            return cached
        text = self._read(self._user_context_path)
        self._cache["user_context"] = text
        return text

    def load_memory_index(self) -> str:
        """Return MEMORY.md verbatim.

        V1 treats this as an index file: it's fed to the model as-is,
        no parsing. Per-memory body files under `memories/` are not
        loaded — see module docstring.
        """
        cached = self._cache.get("memory")
        if cached is not None:
            return cached
        text = self._read(self._memory_md_path)
        self._cache["memory"] = text
        return text

    def invalidate(self) -> None:
        """Drop the in-memory cache; next load re-reads from disk."""
        self._cache.clear()

    # ------------------------------------------------------------------

    def _read(self, path: Path) -> str:
        """Read a file with UTF-8 decoding + defensive byte cap.

        Missing file is NOT an error — returns empty string. Over-cap
        files are truncated to `max_file_bytes` and a WARNING is logged
        so operators can notice + prune.
        """
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return ""
        except OSError:
            log.exception("profile doc read failed: %s", path)
            return ""

        if len(raw) > self._max_bytes:
            log.warning(
                "profile doc %s exceeds cap (%d > %d bytes); truncating",
                path,
                len(raw),
                self._max_bytes,
            )
            raw = raw[: self._max_bytes]
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_retrieved_memories(memories: list[MemoryEntry]) -> str:
    """Render retrieved memories as a bullet list.

    Each line:  `- [<type>] <content> (importance <n>)`
    """
    if not memories:
        return ""
    lines = [
        f"- [{entry.entry_type.value}] {entry.content} (importance {entry.importance})"
        for entry in memories
    ]
    return "\n".join(lines)


def _wrap_section(tag: str, body: str) -> str:
    """Wrap *body* in `<tag>…</tag>` if non-empty, else return ''."""
    body = body.strip()
    if not body:
        return ""
    return f"<{tag}>\n{body}\n</{tag}>"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class StandardContextManager:
    """Default `ContextManager` for companion sessions with memory.

    The assembled system prompt omits any section whose source is empty
    — a missing `MEMORY.md` produces no `<memory_index>` tag, not an
    empty one. `persona_system_prompt` is the final section so
    per-persona guidance has last-word effect.
    """

    def __init__(
        self,
        *,
        loader: ProfileDocLoader,
        conventions: ConventionLoader | None = None,
        base_system_prompt: str = "",
    ) -> None:
        self._loader = loader
        self._conventions = conventions
        self._base_prompt = base_system_prompt

    async def build_request(
        self,
        *,
        session: Session,
        history: list[Message],
        retrieved_memories: list[MemoryEntry],
        tools: list[ToolDefinition],
        cache_aware: bool = False,
    ) -> ProviderRequest:
        """Assemble a `ProviderRequest` from this turn's inputs.

        Args:
            session: The active session — supplies ``model``.
            history: Messages to send (oldest → newest).
            retrieved_memories: Per-turn memory hits.
            tools: Tool catalogue this session can see.
            cache_aware: When True, the system prompt is returned as a
                list of `SystemPromptSegment`s and ``cache_tools`` /
                ``cache_last_message`` are set so cache-supporting
                providers can place breakpoints. When False, the system
                prompt is returned as a flat string and no cache flags
                are set — the legacy path used by models with
                ``supports_prompt_cache=False`` (e.g. local
                OpenAI-compatible servers).
        """
        convention_files = await self._conventions.load() if self._conventions is not None else []
        if cache_aware:
            segments = self._assemble_system_prompt_segments(retrieved_memories, convention_files)
            return ProviderRequest(
                model=session.model,
                messages=list(history),
                system=segments or None,
                tools=tools,
                cache_tools=bool(tools),
                cache_last_message=bool(history),
            )
        system = self._assemble_system_prompt(retrieved_memories, convention_files)
        return ProviderRequest(
            model=session.model,
            messages=list(history),
            system=system or None,
            tools=tools,
        )

    # ------------------------------------------------------------------

    def _assemble_system_prompt(
        self,
        retrieved_memories: list[MemoryEntry],
        convention_files: list[ConventionFile],
    ) -> str:
        sections = [
            _wrap_section("identity", self._loader.load_soul_document()),
            _wrap_section("user_context", self._loader.load_user_context()),
            render_conventions(convention_files),
            _wrap_section("memory_index", self._loader.load_memory_index()),
            _wrap_section(
                "retrieved_memories",
                _render_retrieved_memories(retrieved_memories),
            ),
            _wrap_section("persona_system_prompt", self._base_prompt),
        ]
        return "\n\n".join(s for s in sections if s)

    def _assemble_system_prompt_segments(
        self,
        retrieved_memories: list[MemoryEntry],
        convention_files: list[ConventionFile],
    ) -> list[SystemPromptSegment]:
        """Build the system prompt as cache-aware segments.

        Two segments at most:

        - **Profile-stable** (``cacheable=True``): identity +
          user_context + project_conventions + memory_index. These
          spans only change when soul doc / user-context / convention
          files / MEMORY.md change — rare, user-driven.
        - **Session-stable** (``cacheable=True``): retrieved memories
          + persona system prompt. Skipped entirely when both sources
          are empty so we don't waste a cache breakpoint on whitespace.

        Identity is never empty (the bundled fallback fires when the
        configured soul doc is missing), so segment 1 always exists
        when ``cache_aware=True``.
        """
        profile_sections = [
            _wrap_section("identity", self._loader.load_soul_document()),
            _wrap_section("user_context", self._loader.load_user_context()),
            render_conventions(convention_files),
            _wrap_section("memory_index", self._loader.load_memory_index()),
        ]
        profile_text = "\n\n".join(s for s in profile_sections if s)

        session_sections = [
            _wrap_section(
                "retrieved_memories",
                _render_retrieved_memories(retrieved_memories),
            ),
            _wrap_section("persona_system_prompt", self._base_prompt),
        ]
        session_text = "\n\n".join(s for s in session_sections if s)

        segments: list[SystemPromptSegment] = []
        if profile_text:
            segments.append(SystemPromptSegment(text=profile_text, cacheable=True))
        if session_text:
            segments.append(SystemPromptSegment(text=session_text, cacheable=True))
        return segments
