"""Cairn persistence — SQLite-backed storage for sessions, messages, tool calls, and usage."""

from cairn.persistence._connection import Database
from cairn.persistence._errors import (
    InvalidToolCallTransition,
    MigrationError,
    NotFoundError,
    PersistenceError,
)
from cairn.persistence._messages_repo import MessageRepo
from cairn.persistence._migrations import Migration, apply_pending, discover_migrations
from cairn.persistence._paths import (
    data_dir_for_profile,
    db_path_for_config,
    db_path_for_profile,
)
from cairn.persistence._records import ToolCallRecord, UsageRecord
from cairn.persistence._sessions_repo import SessionRepo
from cairn.persistence._tool_calls_repo import ToolCallRepo
from cairn.persistence._usage_repo import UsageRepo

__all__ = [
    # Connection
    "Database",
    # Errors
    "InvalidToolCallTransition",
    "MigrationError",
    "NotFoundError",
    "PersistenceError",
    # Migrations
    "Migration",
    "apply_pending",
    "discover_migrations",
    # Paths
    "data_dir_for_profile",
    "db_path_for_config",
    "db_path_for_profile",
    # Records
    "ToolCallRecord",
    "UsageRecord",
    # Repos
    "MessageRepo",
    "SessionRepo",
    "ToolCallRepo",
    "UsageRepo",
]
