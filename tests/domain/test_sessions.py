"""Tests for Session model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.domain._enums import SessionType

from .conftest import make_session


class TestSession:
    def test_construction(self) -> None:
        session = make_session()
        assert session.id == "sess-001"
        assert session.type == SessionType.COMPANION
        assert session.memory_space == "companion"

    def test_frozen(self) -> None:
        session = make_session()
        with pytest.raises(ValidationError):
            session.title = "new title"  # type: ignore[misc]

    def test_ephemeral_with_no_memory(self) -> None:
        session = make_session(type="ephemeral", memory_space=None)
        assert session.type == SessionType.EPHEMERAL
        assert session.memory_space is None

    def test_persona_session(self) -> None:
        session = make_session(
            type="persona",
            persona="translator",
            memory_space="persona:translator",
        )
        assert session.type == SessionType.PERSONA
        assert session.memory_space == "persona:translator"

    def test_delegation_sub_session(self) -> None:
        session = make_session(
            type="ephemeral",
            parent_session_id="sess-parent",
            memory_space=None,
        )
        assert session.parent_session_id == "sess-parent"

    def test_defaults(self) -> None:
        session = make_session()
        assert session.title is None
        assert session.archived is False
        assert session.parent_session_id is None
