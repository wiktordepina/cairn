"""Tests for MessageRepo."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from cairn.domain._content import (
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from cairn.domain._messages import Message

from .conftest import make_db_message, make_db_session

if TYPE_CHECKING:
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._sessions_repo import SessionRepo


class TestAppend:
    @pytest.mark.asyncio
    async def test_assigns_idx_in_order(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        m1 = make_db_message(session_id="s1", text="one")
        m2 = make_db_message(session_id="s1", text="two")
        m3 = make_db_message(session_id="s1", text="three")
        await message_repo.append(m1)
        await message_repo.append(m2)
        await message_repo.append(m3)
        assert (m1.idx, m2.idx, m3.idx) == (0, 1, 2)

    @pytest.mark.asyncio
    async def test_persisted_idx_round_trips(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        msg = make_db_message(session_id="s1")
        await message_repo.append(msg)
        loaded = await message_repo.get(msg.id)
        assert loaded is not None
        assert loaded.idx == msg.idx

    @pytest.mark.asyncio
    async def test_fk_violation_on_unknown_session(self, message_repo: MessageRepo) -> None:
        msg = make_db_message(session_id="does-not-exist")
        with pytest.raises(aiosqlite.IntegrityError):
            await message_repo.append(msg)


class TestInsertWithIdx:
    @pytest.mark.asyncio
    async def test_unique_violation(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        a = make_db_message(session_id="s1", id="m-a")
        a.idx = 0
        b = make_db_message(session_id="s1", id="m-b")
        b.idx = 0  # duplicate idx
        await message_repo.insert_with_idx(a)
        with pytest.raises(aiosqlite.IntegrityError):
            await message_repo.insert_with_idx(b)


class TestContentJsonRoundTrip:
    @pytest.mark.asyncio
    async def test_text_block(self, session_repo: SessionRepo, message_repo: MessageRepo) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        msg = make_db_message(session_id="s1", text="hello world")
        await message_repo.append(msg)
        loaded = await message_repo.get(msg.id)
        assert loaded is not None
        assert loaded.get_text() == "hello world"

    @pytest.mark.asyncio
    async def test_all_block_types(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        msg = Message(role="assistant", session_id="s1")
        msg.content.append(TextBlock(text="hi"))
        msg.content.append(ToolUseBlock(id="tc-1", name="fetch", input={"url": "x"}))
        msg.content.append(ImageBlock(source=ImageSource(media_type="image/png", data="abc")))
        await message_repo.append(msg)

        loaded = await message_repo.get(msg.id)
        assert loaded is not None
        assert len(loaded.content) == 3
        assert isinstance(loaded.content[0], TextBlock)
        assert isinstance(loaded.content[1], ToolUseBlock)
        assert isinstance(loaded.content[2], ImageBlock)

    @pytest.mark.asyncio
    async def test_tool_result_block(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        msg = Message(role="user", session_id="s1")
        msg.content.append(ToolResultBlock(tool_use_id="tc-1", content="result", is_error=False))
        await message_repo.append(msg)

        loaded = await message_repo.get(msg.id)
        assert loaded is not None
        block = loaded.content[0]
        assert isinstance(block, ToolResultBlock)
        assert block.content == "result"


class TestListForSession:
    @pytest.mark.asyncio
    async def test_ordered_by_idx(
        self, session_repo: SessionRepo, message_repo: MessageRepo
    ) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        for n in range(5):
            await message_repo.append(make_db_message(session_id="s1", text=str(n)))
        msgs = await message_repo.list_for_session("s1")
        assert [m.idx for m in msgs] == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_after_idx(self, session_repo: SessionRepo, message_repo: MessageRepo) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        for n in range(5):
            await message_repo.append(make_db_message(session_id="s1", text=str(n)))
        msgs = await message_repo.list_for_session("s1", after_idx=2)
        assert [m.idx for m in msgs] == [3, 4]


class TestCountForSession:
    @pytest.mark.asyncio
    async def test_count(self, session_repo: SessionRepo, message_repo: MessageRepo) -> None:
        await session_repo.insert(make_db_session(id="s1"))
        await message_repo.append(make_db_message(session_id="s1"))
        await message_repo.append(make_db_message(session_id="s1"))
        assert (await message_repo.count_for_session("s1")) == 2
