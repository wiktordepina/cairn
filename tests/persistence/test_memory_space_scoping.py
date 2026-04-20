"""Cross-cutting test: memory-space isolation across the persistence layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .conftest import make_db_session

if TYPE_CHECKING:
    from cairn.persistence._sessions_repo import SessionRepo


class TestMemorySpaceScoping:
    @pytest.mark.asyncio
    async def test_companion_excludes_other_spaces(self, session_repo: SessionRepo) -> None:
        await session_repo.insert(make_db_session(id="c1", memory_space="companion"))
        await session_repo.insert(make_db_session(id="w1", memory_space="work"))
        await session_repo.insert(make_db_session(id="t1", memory_space="persona:translator"))
        await session_repo.insert(make_db_session(id="e1", memory_space=None))

        for space, expected in [
            ("companion", {"c1"}),
            ("work", {"w1"}),
            ("persona:translator", {"t1"}),
            (None, {"e1"}),
        ]:
            sessions = await session_repo.list_for_space(space)
            assert {s.id for s in sessions} == expected, (
                f"space={space!r} returned {[s.id for s in sessions]}"
            )

    @pytest.mark.asyncio
    async def test_list_recent_is_explicitly_cross_space(self, session_repo: SessionRepo) -> None:
        """list_recent intentionally crosses memory spaces (global picker)."""
        await session_repo.insert(make_db_session(id="c1", memory_space="companion"))
        await session_repo.insert(make_db_session(id="w1", memory_space="work"))
        await session_repo.insert(make_db_session(id="e1", memory_space=None))

        result = await session_repo.list_recent()
        assert {s.id for s in result} == {"c1", "w1", "e1"}
