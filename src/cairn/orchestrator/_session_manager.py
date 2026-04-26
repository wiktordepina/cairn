"""SessionManager — thin coordinator over `SessionRepo`.

Owns session lifecycle: creation (with memory-space scoping, model
resolution, ID generation), lookup, archival. Does not emit UI events —
that lives in the Orchestrator.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from cairn.domain._enums import SessionType
from cairn.domain._sessions import Session
from cairn.orchestrator._errors import UnknownSession

if TYPE_CHECKING:
    from cairn.config._registry import ModelRegistry
    from cairn.orchestrator._clock import Clock
    from cairn.persistence._sessions_repo import SessionRepo


class SessionManager:
    """Session lifecycle coordinator.

    Applies three invariants at creation time:

    - **Ephemeral sessions never have a memory space.** Callers that pass
      one get it silently dropped — the invariant is on the type, not
      the caller.
    - **Companion sessions default to the `'companion'` space** unless
      the caller explicitly passes one.
    - **Persona sessions accept whatever the caller passes**, including
      `None` (a memoryless persona is legitimate).
    """

    def __init__(
        self,
        *,
        session_repo: SessionRepo,
        model_registry: ModelRegistry,
        clock: Clock,
        default_model_ref: str = "role:primary",
    ) -> None:
        self._repo = session_repo
        self._registry = model_registry
        self._clock = clock
        self._default_model_ref = default_model_ref

    async def create(
        self,
        *,
        type: SessionType,
        persona: str,
        model: str | None = None,
        memory_space: str | None = None,
        parent_session_id: str | None = None,
    ) -> Session:
        """Create and persist a new session. Returns the Session."""
        resolved = self._registry.resolve(model or self._default_model_ref)
        space = self._scope_memory_space(type, memory_space)
        now = self._clock.now()
        session = Session(
            id=uuid.uuid4().hex,
            type=type,
            persona=persona,
            model=resolved.id,
            memory_space=space,
            title=None,
            archived=False,
            parent_session_id=parent_session_id,
            created_at=now,
            updated_at=now,
        )
        await self._repo.insert(session)
        return session

    async def get(self, session_id: str) -> Session:
        """Fetch a session. Raises `UnknownSession` if missing."""
        session = await self._repo.get(session_id)
        if session is None:
            raise UnknownSession(f"No session with id {session_id!r}")
        return session

    async def archive(self, session_id: str) -> None:
        """Archive a session. Raises `UnknownSession` if missing."""
        # Existence check before archive so the error is attributed correctly.
        await self.get(session_id)
        await self._repo.archive(session_id)

    async def update_model(self, session_id: str, model: str) -> Session:
        """Swap the session's active model.

        Used by ``/model`` (user-driven swap) and ``/reload`` (revert
        to config). Returns the refreshed session. Raises
        `UnknownSession` if the row is missing.
        """
        await self.get(session_id)
        await self._repo.update_model(session_id, model)
        return await self.get(session_id)

    @staticmethod
    def _scope_memory_space(type_: SessionType, requested: str | None) -> str | None:
        if type_ is SessionType.EPHEMERAL:
            return None
        if type_ is SessionType.COMPANION:
            return requested or "companion"
        # PERSONA: honour whatever was asked, including None.
        return requested
