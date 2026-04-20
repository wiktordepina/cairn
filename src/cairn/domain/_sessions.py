"""Session model — a frozen record of a conversation context."""

from __future__ import annotations

from datetime import datetime  # noqa: TCH003

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import SessionType  # noqa: TCH001


class Session(BaseModel):
    """A conversation session within cairn.

    Sessions are created once and treated as immutable records. Metadata
    updates (title, archived) produce new instances at the repository level.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: SessionType
    persona: str
    model: str
    memory_space: str | None
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    parent_session_id: str | None = None
