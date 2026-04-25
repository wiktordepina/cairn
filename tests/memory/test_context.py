"""Tests for `StandardContextManager` + `ProfileDocLoader`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import MemoryClass, MemoryEntryType, SessionType
from cairn.domain._memory import MemoryEntry
from cairn.domain._sessions import Session
from cairn.memory._context import (
    DEFAULT_MAX_FILE_BYTES,
    ProfileDocLoader,
    StandardContextManager,
)

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> Session:
    return Session(
        id="sess-1",
        type=SessionType.COMPANION,
        persona="companion",
        model="claude-opus-4-7",
        memory_space="companion",
        created_at=NOW,
        updated_at=NOW,
    )


def _entry(
    *,
    content: str = "user prefers British English",
    entry_type: MemoryEntryType = MemoryEntryType.PREFERENCE,
    importance: int = 7,
) -> MemoryEntry:
    return MemoryEntry(
        id=1,
        memory_space="companion",
        content=content,
        entry_type=entry_type,
        memory_class=MemoryClass.SEMANTIC,
        importance=importance,
        created_at=NOW,
        updated_at=NOW,
    )


def _loader(tmp_path: Path, **overrides: str | int) -> ProfileDocLoader:
    soul = tmp_path / "soul_document.md"
    user_context = tmp_path / "user_context.md"
    memory = tmp_path / "MEMORY.md"
    return ProfileDocLoader(
        soul_document_path=soul,
        user_context_path=user_context,
        memory_md_path=memory,
        **overrides,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# ProfileDocLoader
# ---------------------------------------------------------------------------


class TestProfileDocLoader:
    def test_missing_user_context_returns_empty(self, tmp_path: Path) -> None:
        loader = _loader(tmp_path)
        assert loader.load_user_context() == ""

    def test_missing_memory_returns_empty(self, tmp_path: Path) -> None:
        loader = _loader(tmp_path)
        assert loader.load_memory_index() == ""

    def test_missing_soul_falls_back_to_builtin(self, tmp_path: Path) -> None:
        loader = _loader(tmp_path, builtin_soul_document="BUILT-IN SOUL")  # type: ignore[arg-type]
        assert loader.load_soul_document() == "BUILT-IN SOUL"

    def test_existing_soul_overrides_builtin(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("CUSTOM SOUL", encoding="utf-8")
        loader = _loader(tmp_path, builtin_soul_document="BUILT-IN")  # type: ignore[arg-type]
        assert loader.load_soul_document() == "CUSTOM SOUL"

    def test_memory_md_loaded_verbatim(self, tmp_path: Path) -> None:
        content = (
            "# Memory\n\n"
            "- [Stack and tools](memories/stack.md) — Python, Go, Terraform\n"
            "- [Ketogenic diet](memories/keto.md) — <5g sugar per 100g\n"
        )
        (tmp_path / "MEMORY.md").write_text(content, encoding="utf-8")
        loader = _loader(tmp_path)
        assert loader.load_memory_index() == content

    def test_caching(self, tmp_path: Path) -> None:
        path = tmp_path / "user_context.md"
        path.write_text("first", encoding="utf-8")
        loader = _loader(tmp_path)
        assert loader.load_user_context() == "first"
        path.write_text("second", encoding="utf-8")
        # Cached — disk change not picked up.
        assert loader.load_user_context() == "first"

    def test_invalidate_drops_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "user_context.md"
        path.write_text("first", encoding="utf-8")
        loader = _loader(tmp_path)
        assert loader.load_user_context() == "first"
        path.write_text("second", encoding="utf-8")
        loader.invalidate()
        assert loader.load_user_context() == "second"

    def test_oversized_file_truncated_and_warned(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        oversized = "x" * (DEFAULT_MAX_FILE_BYTES + 500)
        (tmp_path / "user_context.md").write_text(oversized, encoding="utf-8")
        loader = _loader(tmp_path)
        with caplog.at_level(logging.WARNING, logger="cairn.memory._context"):
            out = loader.load_user_context()
        assert len(out) == DEFAULT_MAX_FILE_BYTES
        assert any("truncating" in r.message for r in caplog.records)

    def test_unreadable_file_returns_empty(self, tmp_path: Path) -> None:
        """A read error (EIO, permissions, etc.) logs and returns empty."""

        class _Boom:
            def read_bytes(self) -> bytes:
                raise OSError("permission denied")

        # Monkeypatch the path's read_bytes by subclassing Path is
        # fiddly; instead construct a loader pointing at a directory,
        # which read_bytes will raise IsADirectoryError for.
        dir_path = tmp_path / "a_directory"
        dir_path.mkdir()
        loader = ProfileDocLoader(
            soul_document_path=tmp_path / "soul.md",
            user_context_path=dir_path,  # reading a dir → OSError
            memory_md_path=tmp_path / "MEMORY.md",
        )
        assert loader.load_user_context() == ""


# ---------------------------------------------------------------------------
# StandardContextManager
# ---------------------------------------------------------------------------


class TestStandardContextManager:
    @pytest.mark.asyncio
    async def test_all_three_docs_present_are_wrapped(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("soul body", encoding="utf-8")
        (tmp_path / "user_context.md").write_text("user ctx", encoding="utf-8")
        (tmp_path / "MEMORY.md").write_text("- [a](memories/a.md) hook", encoding="utf-8")

        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<identity>" in req.system
        assert "soul body" in req.system
        assert "<user_context>" in req.system
        assert "user ctx" in req.system
        assert "<memory_index>" in req.system
        assert "[a](memories/a.md)" in req.system

    @pytest.mark.asyncio
    async def test_missing_user_context_omits_section(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("soul body", encoding="utf-8")

        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<user_context>" not in req.system
        assert "<memory_index>" not in req.system

    @pytest.mark.asyncio
    async def test_retrieved_memories_rendered_with_type_and_importance(
        self, tmp_path: Path
    ) -> None:
        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[
                _entry(
                    content="likes keto",
                    entry_type=MemoryEntryType.PREFERENCE,
                    importance=9,
                ),
                _entry(
                    content="owns Cody",
                    entry_type=MemoryEntryType.RELATIONSHIP,
                    importance=10,
                ),
            ],
            tools=[],
        )
        assert req.system is not None
        assert "<retrieved_memories>" in req.system
        assert "- [preference] likes keto (importance 9)" in req.system
        assert "- [relationship] owns Cody (importance 10)" in req.system

    @pytest.mark.asyncio
    async def test_no_memories_no_retrieved_section(self, tmp_path: Path) -> None:
        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<retrieved_memories>" not in req.system

    @pytest.mark.asyncio
    async def test_base_persona_prompt_appended_last(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader, base_system_prompt="You are the Alex persona.")
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        # Persona block must come after identity.
        identity_pos = req.system.index("<identity>")
        persona_pos = req.system.index("<persona_system_prompt>")
        assert identity_pos < persona_pos
        assert "You are the Alex persona." in req.system

    @pytest.mark.asyncio
    async def test_no_sources_falls_back_to_builtin_soul_only(self, tmp_path: Path) -> None:
        """With every file missing and no base prompt, the builtin soul
        document is still emitted — we never ship a totally empty
        system prompt."""
        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<identity>" in req.system
        assert "coding companion" in req.system  # from the builtin

    @pytest.mark.asyncio
    async def test_passes_history_and_tools_through(self, tmp_path: Path) -> None:
        from cairn.domain._messages import Message
        from cairn.domain._provider import ToolDefinition

        loader = _loader(tmp_path)
        mgr = StandardContextManager(loader=loader)
        msg = Message(role="user", session_id="sess-1")
        tools = [
            ToolDefinition(
                name="stub",
                description="d",
                input_schema={"type": "object", "properties": {}},
            )
        ]
        req = await mgr.build_request(
            session=_session(),
            history=[msg],
            retrieved_memories=[],
            tools=tools,
        )
        assert req.messages == [msg]
        assert req.tools == tools
        assert req.model == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_conventions_inserted_between_user_context_and_memory_index(
        self,
        tmp_path: Path,
    ) -> None:
        from cairn.config import ConventionFilesConfig
        from cairn.conventions import AlwaysTrustGate, ConventionLoader

        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        (tmp_path / "user_context.md").write_text("USERCTX", encoding="utf-8")
        (tmp_path / "MEMORY.md").write_text("- [x](m/x.md) hook", encoding="utf-8")

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "AGENTS.md").write_text("PROJECT RULES", encoding="utf-8")

        conventions = ConventionLoader(
            config=ConventionFilesConfig(filenames=["AGENTS.md"]),
            trust_gate=AlwaysTrustGate(),
            cwd=repo,
        )
        mgr = StandardContextManager(loader=_loader(tmp_path), conventions=conventions)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        user_pos = req.system.index("<user_context>")
        conv_pos = req.system.index("<project_conventions")
        mem_pos = req.system.index("<memory_index>")
        assert user_pos < conv_pos < mem_pos
        assert "PROJECT RULES" in req.system
        assert 'source="AGENTS.md"' in req.system

    @pytest.mark.asyncio
    async def test_conventions_none_keeps_pre_brick_layout(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path), conventions=None)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<project_conventions" not in req.system

    @pytest.mark.asyncio
    async def test_empty_convention_load_omits_section(self, tmp_path: Path) -> None:
        from cairn.config import ConventionFilesConfig
        from cairn.conventions import AlwaysTrustGate, ConventionLoader

        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        empty_repo = tmp_path / "empty"
        empty_repo.mkdir()
        (empty_repo / ".git").mkdir()
        conventions = ConventionLoader(
            config=ConventionFilesConfig(filenames=["AGENTS.md"]),
            trust_gate=AlwaysTrustGate(),
            cwd=empty_repo,
        )
        mgr = StandardContextManager(loader=_loader(tmp_path), conventions=conventions)
        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
        )
        assert req.system is not None
        assert "<project_conventions" not in req.system


class TestCacheAwareSegments:
    """Cache-aware path returns SystemPromptSegment list + cache flags."""

    @pytest.mark.asyncio
    async def test_two_segments_when_memories_present(self, tmp_path: Path) -> None:
        from cairn.domain._messages import Message
        from cairn.domain._provider import SystemPromptSegment

        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        (tmp_path / "user_context.md").write_text("USER", encoding="utf-8")
        (tmp_path / "MEMORY.md").write_text("INDEX", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path), base_system_prompt="PERSONA")

        history = [Message(role="user", session_id="sess-1")]
        req = await mgr.build_request(
            session=_session(),
            history=history,
            retrieved_memories=[_entry()],
            tools=[],
            cache_aware=True,
        )

        assert isinstance(req.system, list)
        assert all(isinstance(s, SystemPromptSegment) for s in req.system)
        assert len(req.system) == 2
        seg_profile, seg_session = req.system
        assert seg_profile.cacheable is True
        assert seg_session.cacheable is True
        assert "<identity>" in seg_profile.text
        assert "<user_context>" in seg_profile.text
        assert "<memory_index>" in seg_profile.text
        assert "<retrieved_memories>" in seg_session.text
        assert "<persona_system_prompt>" in seg_session.text

    @pytest.mark.asyncio
    async def test_one_segment_when_no_memories_or_persona(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path))

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
            cache_aware=True,
        )

        assert isinstance(req.system, list)
        assert len(req.system) == 1
        assert req.system[0].cacheable is True
        assert "<identity>" in req.system[0].text

    @pytest.mark.asyncio
    async def test_persona_only_yields_session_segment(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(
            loader=_loader(tmp_path), base_system_prompt="PERSONA"
        )

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
            cache_aware=True,
        )

        assert isinstance(req.system, list)
        assert len(req.system) == 2
        assert "<persona_system_prompt>" in req.system[1].text
        assert "<retrieved_memories>" not in req.system[1].text

    @pytest.mark.asyncio
    async def test_cache_flags_set_when_tools_and_history(self, tmp_path: Path) -> None:
        from cairn.domain._messages import Message
        from cairn.domain._provider import ToolDefinition

        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path))
        history = [Message(role="user", session_id="sess-1")]
        tools = [ToolDefinition(name="x", description="x", input_schema={})]

        req = await mgr.build_request(
            session=_session(),
            history=history,
            retrieved_memories=[],
            tools=tools,
            cache_aware=True,
        )

        assert req.cache_tools is True
        assert req.cache_last_message is True

    @pytest.mark.asyncio
    async def test_cache_flags_off_for_empty_tools_and_history(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path))

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
            cache_aware=True,
        )

        assert req.cache_tools is False
        assert req.cache_last_message is False

    @pytest.mark.asyncio
    async def test_legacy_path_unchanged(self, tmp_path: Path) -> None:
        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        mgr = StandardContextManager(loader=_loader(tmp_path))

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[_entry()],
            tools=[],
            cache_aware=False,
        )

        assert isinstance(req.system, str)
        assert req.cache_tools is False
        assert req.cache_last_message is False

    @pytest.mark.asyncio
    async def test_conventions_in_profile_segment(self, tmp_path: Path) -> None:
        from cairn.config import ConventionFilesConfig
        from cairn.conventions import AlwaysTrustGate, ConventionLoader

        (tmp_path / "soul_document.md").write_text("SOUL", encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "AGENTS.md").write_text("PROJECT RULES", encoding="utf-8")

        conventions = ConventionLoader(
            config=ConventionFilesConfig(filenames=["AGENTS.md"]),
            trust_gate=AlwaysTrustGate(),
            cwd=repo,
        )
        mgr = StandardContextManager(loader=_loader(tmp_path), conventions=conventions)

        req = await mgr.build_request(
            session=_session(),
            history=[],
            retrieved_memories=[],
            tools=[],
            cache_aware=True,
        )

        assert isinstance(req.system, list)
        # Conventions live in segment 1 (profile-stable) per design.
        assert "<project_conventions" in req.system[0].text
