"""Shared fixtures for config tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def minimal_model_dict(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid model config dict, with optional overrides."""
    base: dict[str, Any] = {
        "id": "test-model",
        "provider": "anthropic",
        "display_name": "Test Model",
        "context_window": 200_000,
        "max_output_tokens": 8_000,
        "supports_tools": True,
        "input_cost_per_1m": 1.0,
        "output_cost_per_1m": 5.0,
    }
    base.update(overrides)
    return base


def minimal_profile_dict(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid profile config dict, with optional overrides."""
    base: dict[str, Any] = {
        "soul_document_path": "/tmp/soul.md",
        "user_context_path": "/tmp/user.md",
        "memory_md_path": "/tmp/MEMORY.md",
        "primary_model": "role:primary",
        "utility_model": "role:utility",
    }
    base.update(overrides)
    return base


def full_config_dict(**overrides: Any) -> dict[str, Any]:
    """Return a full valid config dict matching the arch doc example."""
    base: dict[str, Any] = {
        "schema_version": 1,
        "active_profile": "personal",
        "providers": {
            "anthropic": {
                "api_key": "keyring:cairn:anthropic-api-key",
            },
        },
        "models": [
            minimal_model_dict(
                id="claude-opus-4-7",
                display_name="Claude Opus 4.7",
                context_window=200_000,
                max_output_tokens=32_000,
                input_cost_per_1m=15.0,
                output_cost_per_1m=75.0,
                roles=["primary", "reasoning"],
            ),
            minimal_model_dict(
                id="claude-haiku-4-5",
                display_name="Claude Haiku 4.5",
                max_output_tokens=8_000,
                input_cost_per_1m=1.0,
                output_cost_per_1m=5.0,
                roles=["utility", "fast"],
            ),
        ],
        "profiles": {
            "personal": minimal_profile_dict(name="pendragon"),
        },
    }
    base.update(overrides)
    return base


SAMPLE_TOML = """\
schema_version = 1
active_profile = "personal"

[providers.anthropic]
api_key = "env:ANTHROPIC_API_KEY"

[[models]]
id = "claude-opus-4-7"
provider = "anthropic"
display_name = "Claude Opus 4.7"
context_window = 200_000
max_output_tokens = 32_000
supports_tools = true
supports_vision = true
supports_prompt_cache = true
input_cost_per_1m = 15.00
output_cost_per_1m = 75.00
roles = ["primary", "reasoning"]

[[models]]
id = "claude-haiku-4-5"
provider = "anthropic"
display_name = "Claude Haiku 4.5"
context_window = 200_000
max_output_tokens = 8_000
supports_tools = true
input_cost_per_1m = 1.00
output_cost_per_1m = 5.00
roles = ["utility", "fast"]

[profiles.personal]
name = "pendragon"
soul_document_path = "/tmp/soul.md"
user_context_path = "/tmp/user.md"
memory_md_path = "/tmp/MEMORY.md"
primary_model = "role:primary"
utility_model = "role:utility"
"""

PROJECT_OVERRIDE_TOML = """\
schema_version = 1
active_profile = "personal"

[profiles.personal]
primary_model = "claude-haiku-4-5"
"""

LOCAL_OVERRIDE_TOML = """\
schema_version = 1

[profiles.personal.budgets]
per_turn_usd = 1.00
"""


@pytest.fixture
def config_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create a temporary config directory structure with sample files."""
    user_dir = tmp_path / "user" / "cairn"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text(SAMPLE_TOML)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Create a fake git repo
    (project_dir / ".git").mkdir()

    cairn_dir = project_dir / ".cairn"
    cairn_dir.mkdir()
    (cairn_dir / "config.toml").write_text(PROJECT_OVERRIDE_TOML)
    (cairn_dir / "config.local.toml").write_text(LOCAL_OVERRIDE_TOML)

    return {
        "user": user_dir,
        "project": project_dir,
        "user_config": user_dir / "config.toml",
        "project_config": cairn_dir / "config.toml",
        "local_config": cairn_dir / "config.local.toml",
    }
