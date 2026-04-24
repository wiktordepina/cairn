"""Tests for the `UIConfig` pydantic model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.config import UIConfig


class TestUIConfigDefaults:
    def test_defaults(self) -> None:
        config = UIConfig()
        assert config.theme == "auto"
        assert config.session_type_colours == {}
        assert config.show_cost_in_header is True
        assert config.max_chat_log_messages == 500

    def test_theme_validation(self) -> None:
        with pytest.raises(ValidationError):
            UIConfig(theme="neon")  # pyright: ignore[reportArgumentType]

    def test_accepts_valid_session_type_colours(self) -> None:
        config = UIConfig(
            session_type_colours={
                "companion": "#ff00aa",
                "persona": "#123456",
            }
        )
        assert config.session_type_colours["companion"] == "#ff00aa"

    def test_rejects_wrong_length_hex(self) -> None:
        with pytest.raises(ValidationError, match="hex"):
            UIConfig(session_type_colours={"companion": "#fff"})

    def test_rejects_no_hash_prefix(self) -> None:
        with pytest.raises(ValidationError, match="hex"):
            UIConfig(session_type_colours={"companion": "ff00aa"})

    def test_rejects_non_hex_characters(self) -> None:
        with pytest.raises(ValidationError, match="not valid hex"):
            UIConfig(session_type_colours={"companion": "#zzzzzz"})

    def test_is_frozen(self) -> None:
        config = UIConfig()
        with pytest.raises(ValidationError):
            config.theme = "dark"  # pyright: ignore[reportAttributeAccessIssue]
