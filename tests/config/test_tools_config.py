"""Tests for the `ToolsConfig` pydantic model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.config import ToolsConfig


class TestToolsConfigDefaults:
    def test_defaults_empty_overrides(self) -> None:
        config = ToolsConfig()
        assert config.timeout_s_overrides == {}

    def test_accepts_valid_overrides(self) -> None:
        config = ToolsConfig(timeout_s_overrides={"web_fetch": 90.0, "delegation": 300.0})
        assert config.timeout_s_overrides["web_fetch"] == 90.0
        assert config.timeout_s_overrides["delegation"] == 300.0

    def test_accepts_lower_bound(self) -> None:
        config = ToolsConfig(timeout_s_overrides={"x": 1.0})
        assert config.timeout_s_overrides["x"] == 1.0

    def test_accepts_upper_bound(self) -> None:
        config = ToolsConfig(timeout_s_overrides={"x": 3600.0})
        assert config.timeout_s_overrides["x"] == 3600.0

    def test_rejects_below_lower_bound(self) -> None:
        with pytest.raises(ValidationError, match="between 1.0 and 3600.0"):
            ToolsConfig(timeout_s_overrides={"file_read": 0.5})

    def test_rejects_above_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="between 1.0 and 3600.0"):
            ToolsConfig(timeout_s_overrides={"web_fetch": 4000.0})

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValidationError, match="between 1.0 and 3600.0"):
            ToolsConfig(timeout_s_overrides={"x": 0.0})

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="between 1.0 and 3600.0"):
            ToolsConfig(timeout_s_overrides={"x": -10.0})

    def test_unknown_tool_name_does_not_fail_validation(self) -> None:
        # Forward-compat: a TOML referencing a tool that doesn't exist
        # in this build should load. The runner is the only place that
        # knows valid names; warning is deferred to runner construction.
        config = ToolsConfig(timeout_s_overrides={"a_tool_we_have_never_heard_of": 30.0})
        assert config.timeout_s_overrides["a_tool_we_have_never_heard_of"] == 30.0

    def test_is_frozen(self) -> None:
        config = ToolsConfig()
        with pytest.raises(ValidationError):
            config.timeout_s_overrides = {"x": 5.0}  # pyright: ignore[reportAttributeAccessIssue]

    def test_profile_config_includes_default_tools(self) -> None:
        from cairn.config import ProfileConfig

        config = ProfileConfig(
            soul_document_path="/tmp/soul.md",  # pyright: ignore[reportArgumentType]
            user_context_path="/tmp/uc.md",  # pyright: ignore[reportArgumentType]
            memory_md_path="/tmp/m.md",  # pyright: ignore[reportArgumentType]
            primary_model="m",
            utility_model="m",
        )
        assert config.tools == ToolsConfig()
        assert config.tools.timeout_s_overrides == {}
