-- Migration tracking
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL  -- ISO 8601 UTC
);

-- Sessions
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    persona TEXT NOT NULL,
    model TEXT NOT NULL,
    memory_space TEXT,
    title TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    parent_session_id TEXT REFERENCES sessions(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_sessions_type_updated ON sessions(type, updated_at DESC);
CREATE INDEX idx_sessions_memory_space ON sessions(memory_space) WHERE memory_space IS NOT NULL;
CREATE INDEX idx_sessions_parent ON sessions(parent_session_id) WHERE parent_session_id IS NOT NULL;
CREATE INDEX idx_sessions_archived_updated ON sessions(archived, updated_at DESC);

-- Messages
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, idx)
);
CREATE INDEX idx_messages_session_idx ON messages(session_id, idx);

-- Tool calls
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    tool_kind TEXT NOT NULL,
    server TEXT,
    input_json TEXT NOT NULL,
    input_truncated INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    output_bytes INTEGER,
    status TEXT NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0,
    error_class TEXT,
    error_message TEXT,
    is_delegation INTEGER NOT NULL DEFAULT 0,
    delegation_session_id TEXT REFERENCES sessions(id),
    approval_required INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER
);
CREATE INDEX idx_tool_calls_session ON tool_calls(session_id, started_at);
CREATE INDEX idx_tool_calls_message ON tool_calls(message_id);
CREATE INDEX idx_tool_calls_status ON tool_calls(status);

-- Model usage
CREATE TABLE model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT REFERENCES sessions(id),
    message_id TEXT REFERENCES messages(id),
    parent_session_id TEXT REFERENCES sessions(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    duration_ms INTEGER,
    stop_reason TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT
);
CREATE INDEX idx_usage_timestamp ON model_usage(timestamp);
CREATE INDEX idx_usage_session ON model_usage(session_id, timestamp);
CREATE INDEX idx_usage_operation ON model_usage(operation, timestamp);
CREATE INDEX idx_usage_parent ON model_usage(parent_session_id) WHERE parent_session_id IS NOT NULL;
