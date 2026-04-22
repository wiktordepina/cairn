"""Repository for `memory_entries` and its FTS5 shadow.

Tier-1 observation storage for the memory brick. The key non-obvious
piece is `store()`: rather than naive insert, it runs a dedup check
against recent near-duplicates and refreshes the existing row instead
of inserting a second one. This is what prevents the observation log
from drowning in repeated "user prefers keto" lines when the same fact
surfaces across many turns.

Spec: `.plan/memory-brick-design.md` Phase 1; arch doc §4.11.
"""

from __future__ import annotations

from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from cairn.domain._enums import MemoryClass, MemoryEntryType
from cairn.domain._memory import MemoryEntry
from cairn.persistence._records import MemoryHit
from cairn.persistence._text import significant_words

if TYPE_CHECKING:
    import aiosqlite

    from cairn.persistence._connection import Database


_SELECT_COLS = (
    "id, memory_space, content, entry_type, memory_class, importance, "
    "source_session_id, source_message_id, created_at, updated_at"
)

# Tuning knobs baked into the repo. Surfaced as top-level constants so
# tests can monkeypatch rather than go through a config object; these
# are implementation details of dedup, not user-visible policy.
_DEDUP_CANDIDATE_LIMIT = 20
_DEDUP_SIMILARITY_THRESHOLD = 0.90


def _row_to_entry(row: aiosqlite.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        memory_space=row["memory_space"],
        content=row["content"],
        entry_type=MemoryEntryType(row["entry_type"]),
        memory_class=MemoryClass(row["memory_class"]),
        importance=row["importance"],
        source_session_id=row["source_session_id"],
        source_message_id=row["source_message_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _fts_or_query(words: list[str]) -> str:
    """Build an FTS5 MATCH expression that ORs quoted significant words.

    Each token is wrapped in double quotes to neutralise FTS5 operators
    (e.g. a token that happens to be "AND" or contains `:`). OR-joined
    so the candidate query casts a wide net; `SequenceMatcher` does the
    strict filtering.
    """
    return " OR ".join(f'"{w}"' for w in words)


class MemoryRepo:
    """Tier-1 memory storage with dedup-on-store and FTS5 search."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def store(
        self,
        *,
        memory_space: str,
        content: str,
        entry_type: MemoryEntryType,
        memory_class: MemoryClass,
        importance: int = 5,
        source_session_id: str | None = None,
        source_message_id: str | None = None,
        now: datetime | None = None,
    ) -> MemoryEntry:
        """Persist a new observation, or refresh a near-duplicate.

        Dedup flow (arch doc §4.11):
        1. Pull significant words from *content*.
        2. FTS5 candidate query filtered by space + entry_type.
        3. Compare each candidate with `SequenceMatcher.ratio()`.
        4. If ≥ 0.90: update `updated_at` + `MAX(importance)`; return
           the refreshed existing row.
        5. Otherwise: insert a new row.

        Runs inside a single `BEGIN IMMEDIATE` so concurrent stores
        can't race-insert near-duplicates.

        Raises:
            ValueError: if *memory_space* is empty or *importance* is
                outside 1..10. (Schema-level CHECK constraints catch
                this too; we raise early for clarity.)
        """
        if not memory_space:
            raise ValueError("memory_space must be a non-empty string")
        if not 1 <= importance <= 10:
            raise ValueError(f"importance must be between 1 and 10, got {importance}")
        if not content.strip():
            raise ValueError("content must be non-empty")

        now = now or datetime.now(UTC)
        words = significant_words(content)

        async with self._db.transaction() as conn:
            existing = await self._find_duplicate(
                conn,
                memory_space=memory_space,
                entry_type=entry_type,
                content=content,
                words=words,
            )

            if existing is not None:
                merged_importance = max(existing.importance, importance)
                await conn.execute(
                    "UPDATE memory_entries SET updated_at = ?, importance = ? WHERE id = ?",
                    (now.isoformat(), merged_importance, existing.id),
                )
                return existing.model_copy(
                    update={"importance": merged_importance, "updated_at": now},
                )

            cursor = await conn.execute(
                """
                INSERT INTO memory_entries
                    (memory_space, content, entry_type, memory_class,
                     importance, source_session_id, source_message_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_space,
                    content,
                    entry_type.value,
                    memory_class.value,
                    importance,
                    source_session_id,
                    source_message_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
            await cursor.close()

        return MemoryEntry(
            id=new_id,
            memory_space=memory_space,
            content=content,
            entry_type=entry_type,
            memory_class=memory_class,
            importance=importance,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            created_at=now,
            updated_at=now,
        )

    async def _find_duplicate(
        self,
        conn: aiosqlite.Connection,
        *,
        memory_space: str,
        entry_type: MemoryEntryType,
        content: str,
        words: list[str],
    ) -> MemoryEntry | None:
        """Scan up to N FTS5 candidates; return the first that clears
        the similarity threshold, else None.

        If *words* is empty (content is all stopwords / punctuation),
        dedup is skipped — we couldn't build a meaningful FTS query.
        """
        if not words:
            return None

        match_expr = _fts_or_query(words)
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM memory_entries
            WHERE id IN (
                SELECT rowid FROM memory_entries_fts
                WHERE memory_entries_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            )
            AND memory_space = ?
            AND entry_type = ?
            """,
            (match_expr, _DEDUP_CANDIDATE_LIMIT, memory_space, entry_type.value),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        for row in rows:
            ratio = SequenceMatcher(None, content, row["content"]).ratio()
            if ratio >= _DEDUP_SIMILARITY_THRESHOLD:
                return _row_to_entry(row)
        return None

    async def search(
        self,
        memory_space: str,
        query: str,
        *,
        k: int = 8,
        entry_types: list[MemoryEntryType] | None = None,
    ) -> list[MemoryHit]:
        """FTS5 MATCH over *query*, scoped to *memory_space*.

        The retrieval service rescores results with recency + importance
        weights; here we just return the raw BM25-ranked hits.
        """
        words = significant_words(query)
        if not words:
            return []

        match_expr = _fts_or_query(words)
        params: list[object] = [match_expr, memory_space]
        type_clause = ""
        if entry_types:
            placeholders = ",".join("?" for _ in entry_types)
            type_clause = f" AND e.entry_type IN ({placeholders})"
            params.extend(t.value for t in entry_types)
        params.append(k)

        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {", ".join(f"e.{c}" for c in _SELECT_COLS.split(", "))},
                   bm25(memory_entries_fts) AS bm25_score
            FROM memory_entries_fts
            JOIN memory_entries e ON e.id = memory_entries_fts.rowid
            WHERE memory_entries_fts MATCH ?
              AND e.memory_space = ?
              {type_clause}
            ORDER BY bm25_score
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [MemoryHit(entry=_row_to_entry(r), bm25_score=r["bm25_score"]) for r in rows]

    async def recent(self, memory_space: str, *, limit: int = 50) -> list[MemoryEntry]:
        """Return the most recently-updated entries for *memory_space*."""
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM memory_entries
            WHERE memory_space = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (memory_space, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_entry(r) for r in rows]

    async def get(self, entry_id: int) -> MemoryEntry | None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM memory_entries WHERE id = ?",
            (entry_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_entry(row) if row is not None else None

    async def delete(self, entry_id: int) -> None:
        """Hard-delete an entry. Used by tooling, not the extraction path."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))

    async def count_for_space(self, memory_space: str) -> int:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_entries WHERE memory_space = ?",
            (memory_space,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row is not None else 0
