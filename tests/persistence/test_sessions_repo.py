"""Tests for SessionRepo."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import SessionType

from .conftest import make_db_session

if TYPE_CHECKING:
    from cairn.persistence._sessions_repo import SessionRepo


class TestInsertAndGet:
    @pytest.mark.asyncio
    async def test_round_trip(self, session_repo: SessionRepo) -> None:
        session = make_db_session(id="s1", title="Test session")
        await session_repo.insert(session)
        loaded = await session_repo.get("s1")
        assert loaded is not None
        assert loaded.id == "s1"
        assert loaded.title == "Test session"
        assert loaded.type == SessionType.COMPANION

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, session_repo: SessionRepo) -> None:
        assert (await session_repo.get("nonexistent")) is None


class TestUpdateMetadata:
    @pytest.mark.asyncio
    async def test_set_title(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        await session_repo.update_metadata("s1", title="Renamed")
        loaded = await session_repo.get("s1")
        assert loaded is not None
        assert loaded.title == "Renamed"

    @pytest.mark.asyncio
    async def test_archive_flag(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        await session_repo.archive("s1")
        loaded = await session_repo.get("s1")
        assert loaded is not None
        assert loaded.archived is True

    @pytest.mark.asyncio
    async def test_updated_at_auto_bumps(self, session_repo: SessionRepo) -> None:
        original_time = datetime(2026, 1, 1, tzinfo=UTC)
        await session_repo.insert(
            make_db_session(id="s1", created_at=original_time, updated_at=original_time)
        )
        await session_repo.update_metadata("s1", title="x")
        loaded = await session_repo.get("s1")
        assert loaded is not None
        assert loaded.updated_at > original_time


class TestListForSpace:
    @pytest.mark.asyncio
    async def test_filters_by_space(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="c1", memory_space="companion"))
        await session_repo.insert(make_db_session(id="w1", memory_space="work"))
        await session_repo.insert(make_db_session(id="e1", memory_space=None))

        companions = await session_repo.list_for_space("companion")
        assert {s.id for s in companions} == {"c1"}

    @pytest.mark.asyncio
    async def test_none_matches_memoryless(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="c1", memory_space="companion"))
        await session_repo.insert(make_db_session(id="e1", memory_space=None))

        memoryless = await session_repo.list_for_space(None)
        assert {s.id for s in memoryless} == {"e1"}

    @pytest.mark.asyncio
    async def test_archived_excluded_by_default(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="active"))
        await session_repo.insert(make_db_session(id="archived", archived=True))

        result = await session_repo.list_for_space("companion")
        assert {s.id for s in result} == {"active"}

        with_archived = await session_repo.list_for_space("companion", include_archived=True)
        assert {s.id for s in with_archived} == {"active", "archived"}


class TestListRecent:
    @pytest.mark.asyncio
    async def test_crosses_spaces(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="c1", memory_space="companion"))
        await session_repo.insert(make_db_session(id="w1", memory_space="work"))

        result = await session_repo.list_recent()
        assert {s.id for s in result} == {"c1", "w1"}

    @pytest.mark.asyncio
    async def test_filter_by_type(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="c1", type="companion"))
        await session_repo.insert(make_db_session(id="e1", type="ephemeral", memory_space=None))
        result = await session_repo.list_recent(type=SessionType.EPHEMERAL)
        assert {s.id for s in result} == {"e1"}


class TestChildrenOf:
    @pytest.mark.asyncio
    async def test_returns_children(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="parent"))
        await session_repo.insert(
            make_db_session(id="child1", parent_session_id="parent", memory_space=None)
        )
        await session_repo.insert(
            make_db_session(id="child2", parent_session_id="parent", memory_space=None)
        )
        children = await session_repo.children_of("parent")
        assert {s.id for s in children} == {"child1", "child2"}
