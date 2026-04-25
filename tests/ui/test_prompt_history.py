"""Unit tests for the per-project prompt-history store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.ui._prompt_history import PromptHistoryStore

if TYPE_CHECKING:
    from pathlib import Path


class TestPromptHistoryStore:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        store = PromptHistoryStore(path=tmp_path / "nope.jsonl", max_size=100)
        assert store.load() == []

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        store = PromptHistoryStore(path=tmp_path / "h.jsonl", max_size=100)
        store.save(["one", "two", "three"])
        assert store.load() == ["one", "two", "three"]

    def test_load_trims_to_max_size(self, tmp_path: Path) -> None:
        path = tmp_path / "h.jsonl"
        full = PromptHistoryStore(path=path, max_size=100)
        full.save(["a", "b", "c", "d"])
        small = PromptHistoryStore(path=path, max_size=2)
        assert small.load() == ["c", "d"]

    def test_save_trims_to_max_size(self, tmp_path: Path) -> None:
        store = PromptHistoryStore(path=tmp_path / "h.jsonl", max_size=2)
        store.save(["a", "b", "c", "d"])
        # Reading without trimming on load (large cap) shows what was written.
        rehydrate = PromptHistoryStore(path=store.path, max_size=100)
        assert rehydrate.load() == ["c", "d"]

    def test_corrupt_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "h.jsonl"
        path.write_text(
            '{"prompt": "good"}\nnot-json\n{"prompt": "also good"}\n',
            encoding="utf-8",
        )
        store = PromptHistoryStore(path=path, max_size=10)
        assert store.load() == ["good", "also good"]

    def test_disabled_store_returns_empty_and_skips_save(self, tmp_path: Path) -> None:
        path = tmp_path / "h.jsonl"
        store = PromptHistoryStore(path=path, max_size=0)
        store.save(["whatever"])
        assert not path.exists()
        assert store.load() == []
