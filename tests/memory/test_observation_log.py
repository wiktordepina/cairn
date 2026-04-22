"""Tests for the JSONL observation log."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from cairn.domain._enums import MemoryClass, MemoryEntryType
from cairn.domain._memory import MemoryEntry
from cairn.memory._observation_log import Observation, ObservationLog

if TYPE_CHECKING:
    from pathlib import Path


def _obs(*, id: int = 1, content: str = "hello world") -> Observation:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    return Observation(
        id=id,
        memory_space="companion",
        content=content,
        entry_type=MemoryEntryType.FACT,
        memory_class=MemoryClass.SEMANTIC,
        importance=5,
        created_at=now,
        updated_at=now,
    )


class TestAppendRoundTrip:
    @pytest.mark.asyncio
    async def test_single_append_and_reparse(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        when = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        path = await log.append(_obs(id=7, content="a single fact"), now=when)

        assert path == tmp_path / "observations" / "2026-04-22.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        round_trip = Observation.model_validate_json(lines[0])
        assert round_trip.id == 7
        assert round_trip.content == "a single fact"

    @pytest.mark.asyncio
    async def test_multiple_appends_same_day(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        when = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        for i in range(5):
            await log.append(_obs(id=i, content=f"fact {i}"), now=when)
        path = tmp_path / "observations" / "2026-04-22.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5
        ids = [json.loads(line)["id"] for line in lines]
        assert ids == [0, 1, 2, 3, 4]


class TestDayRollover:
    @pytest.mark.asyncio
    async def test_new_day_creates_new_file(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        day1 = datetime(2026, 4, 22, 23, 59, tzinfo=UTC)
        day2 = datetime(2026, 4, 23, 0, 0, tzinfo=UTC)

        await log.append(_obs(id=1, content="end of day one"), now=day1)
        await log.append(_obs(id=2, content="start of day two"), now=day2)

        obs_dir = tmp_path / "observations"
        files = sorted(p.name for p in obs_dir.iterdir())
        assert files == ["2026-04-22.jsonl", "2026-04-23.jsonl"]

    @pytest.mark.asyncio
    async def test_non_utc_input_routed_to_utc_day(self, tmp_path: Path) -> None:
        """A timestamp supplied in a non-UTC timezone should route to the
        corresponding UTC day-file, not the local day."""
        log = ObservationLog(tmp_path)
        # 23:30 in UTC+6 == 17:30 UTC the same date, so target file =
        # 2026-04-22.jsonl
        tz = timezone(timedelta(hours=6))
        when = datetime(2026, 4, 22, 23, 30, tzinfo=tz)
        path = await log.append(_obs(id=1), now=when)
        assert path.name == "2026-04-22.jsonl"


class TestDirectoryPermissions:
    @pytest.mark.asyncio
    async def test_observations_dir_created_0o700(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        await log.append(_obs(id=1), now=datetime(2026, 4, 22, 12, 0, tzinfo=UTC))
        obs_dir = tmp_path / "observations"
        assert obs_dir.is_dir()
        mode = stat.S_IMODE(os.stat(obs_dir).st_mode)
        assert mode == 0o700

    @pytest.mark.asyncio
    async def test_nested_root_created(self, tmp_path: Path) -> None:
        """Root itself doesn't need to exist up-front; the log creates
        the full `<root>/observations/` tree on first append."""
        root = tmp_path / "data" / "cairn" / "default" / "memory"
        log = ObservationLog(root)
        assert not root.exists()
        await log.append(_obs(id=1), now=datetime(2026, 4, 22, 12, 0, tzinfo=UTC))
        assert (root / "observations").is_dir()


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_appends_dont_interleave(self, tmp_path: Path) -> None:
        log = ObservationLog(tmp_path)
        when = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)

        # Use long, distinctive payloads so any byte-level interleaving
        # would produce invalid JSON on one of the lines.
        payloads = [
            _obs(id=i, content=f"observation body marker {i:04d} " + "padding word " * 40)
            for i in range(50)
        ]
        await asyncio.gather(*(log.append(o, now=when) for o in payloads))

        path = tmp_path / "observations" / "2026-04-22.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 50
        # Every line must reparse; this catches partial-write interleaving.
        ids = {Observation.model_validate_json(line).id for line in lines}
        assert ids == set(range(50))


class TestObservationFromEntry:
    def test_round_trip_from_entry(self) -> None:
        now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        entry = MemoryEntry(
            id=42,
            memory_space="companion",
            content="a fact",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            importance=6,
            created_at=now,
            updated_at=now,
        )
        obs = Observation.from_entry(entry)
        assert obs.id == 42
        assert obs.content == "a fact"
        assert obs.importance == 6

    def test_from_entry_rejects_unpersisted(self) -> None:
        now = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        entry = MemoryEntry(
            memory_space="companion",
            content="a fact",
            entry_type=MemoryEntryType.FACT,
            memory_class=MemoryClass.SEMANTIC,
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(ValueError, match="unpersisted"):
            Observation.from_entry(entry)


class TestPathHelper:
    def test_memory_dir_for_profile(self) -> None:
        from cairn.persistence._paths import memory_dir_for_profile

        path = memory_dir_for_profile("default")
        assert path.name == "memory"
        assert path.parent.name == "default"
