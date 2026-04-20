-- Turns — one row per turn attempted.
-- State column is authoritative; transitions are conditional UPDATEs driven by
-- the Orchestrator. Non-terminal rows at startup indicate a prior crash and
-- are marked aborted by resume_aborted_turns().
CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    aborted_reason TEXT,
    stop_reason TEXT
);
CREATE INDEX idx_turns_session_started ON turns(session_id, started_at);
CREATE INDEX idx_turns_nonterminal ON turns(state) WHERE state NOT IN ('completed', 'aborted');

-- Approval decisions — one row per approval event for a tool call.
-- For the security-doc audit trail (timestamp, tool, args-summary, approved-by).
-- A tool call can have multiple rows if approvers escalate / user revises, but
-- in V1 each tool call gets exactly one terminal decision.
CREATE TABLE approval_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
    decided_at TEXT NOT NULL,
    decided_by TEXT NOT NULL,        -- 'user', 'auto:read-only', 'session-allowlist', etc.
    decision TEXT NOT NULL,          -- 'approved' | 'rejected'
    reason TEXT,                     -- populated for rejections
    args_snapshot_json TEXT          -- what the user saw (redacted)
);
CREATE INDEX idx_approval_tool_call ON approval_decisions(tool_call_id);
CREATE INDEX idx_approval_decided_at ON approval_decisions(decided_at);

-- turn_id threading — every row produced during a turn carries its turn_id.
-- Enables "cost per turn" queries, clean joins from turns to downstream artefacts,
-- and debugging replays.
--
-- NB: deliberately not a foreign key. The turns row references the user message
-- (turns.user_message_id → messages.id), and the user message also carries the
-- turn_id for uniform threading. Making messages.turn_id a hard FK would
-- introduce a circular write dependency: the message cannot land before the
-- turn, and the turn cannot land before the message. Logical integrity is
-- enforced by the orchestrator at write time.
ALTER TABLE messages ADD COLUMN turn_id TEXT;
ALTER TABLE tool_calls ADD COLUMN turn_id TEXT;
ALTER TABLE model_usage ADD COLUMN turn_id TEXT;

CREATE INDEX idx_messages_turn ON messages(turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX idx_tool_calls_turn ON tool_calls(turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX idx_usage_turn ON model_usage(turn_id) WHERE turn_id IS NOT NULL;
