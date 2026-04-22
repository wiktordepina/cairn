-- Memory entries — tier-1 observations extracted post-turn by the memory brick.
-- Content is the raw natural-language observation; entry_type classifies it
-- (fact / preference / insight / relationship / event / task); memory_class
-- drives recency decay (semantic vs episodic half-life).
--
-- source_session_id / source_message_id link back to the conversation the
-- observation was extracted from. They do NOT cascade on delete: memory
-- outlives sessions (arch doc §4.11 invariant #4).
CREATE TABLE memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_space TEXT NOT NULL,
    content TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (
        entry_type IN ('fact', 'preference', 'insight', 'relationship', 'event', 'task')
    ),
    memory_class TEXT NOT NULL CHECK (memory_class IN ('semantic', 'episodic')),
    importance INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    source_session_id TEXT REFERENCES sessions(id),
    source_message_id TEXT REFERENCES messages(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_mem_space ON memory_entries(memory_space);
CREATE INDEX idx_mem_type ON memory_entries(memory_space, entry_type);
CREATE INDEX idx_mem_created ON memory_entries(memory_space, created_at);
CREATE INDEX idx_mem_importance ON memory_entries(memory_space, importance);

-- External-content FTS5 shadow index. Keeps the primary row in memory_entries
-- and mirrors only what the search path needs; triggers below keep it in sync.
CREATE VIRTUAL TABLE memory_entries_fts USING fts5(
    content,
    entry_type UNINDEXED,
    memory_space UNINDEXED,
    content=memory_entries,
    content_rowid=id,
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER memory_entries_ai AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_entries_fts(rowid, content, entry_type, memory_space)
    VALUES (new.id, new.content, new.entry_type, new.memory_space);
END;

CREATE TRIGGER memory_entries_ad AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_entries_fts(memory_entries_fts, rowid, content, entry_type, memory_space)
    VALUES ('delete', old.id, old.content, old.entry_type, old.memory_space);
END;

CREATE TRIGGER memory_entries_au AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_entries_fts(memory_entries_fts, rowid, content, entry_type, memory_space)
    VALUES ('delete', old.id, old.content, old.entry_type, old.memory_space);
    INSERT INTO memory_entries_fts(rowid, content, entry_type, memory_space)
    VALUES (new.id, new.content, new.entry_type, new.memory_space);
END;
