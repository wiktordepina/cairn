"""Memory entry — a single observation retrieved from the memory store.

The memory brick adds full persistence + FTS + scoring; this module owns
the domain shape so that the orchestrator and context manager can be
written against it before memory itself lands.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — used by Pydantic at runtime

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import MemoryClass, MemoryEntryType  # noqa: TCH001


class MemoryEntry(BaseModel):
    """A single retrieved memory observation.

    Immutable once constructed. The `id` is assigned when the entry is
    persisted; retrieved entries carry an integer; not-yet-persisted
    entries carry `None`.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    memory_space: str
    content: str
    entry_type: MemoryEntryType
    memory_class: MemoryClass
    importance: int = 5
    source_session_id: str | None = None
    source_message_id: str | None = None
    created_at: datetime
    updated_at: datetime
