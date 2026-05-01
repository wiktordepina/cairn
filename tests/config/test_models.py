"""Tests for Pydantic config models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.config._models import (
    BudgetConfig,
    CairnConfig,
    MemoryConfig,
    ModelConfig,
    ModelRole,
    ProfileConfig,
    ProviderConfig,
    SecretRef,
)

from .conftest import full_config_dict, minimal_model_dict, minimal_profile_dict


class TestSecretRef:
    def test_parse_keyring(self) -> None:
        ref = SecretRef.parse("keyring:cairn:api-key")
        assert ref.scheme == "keyring"
        assert ref.params == ("cairn", "api-key")

    def test_parse_env(self) -> None:
        ref = SecretRef.parse("env:OPENAI_API_KEY")
        assert ref.scheme == "env"
        assert ref.params == ("OPENAI_API_KEY",)

    def test_parse_prompt_default(self) -> None:
        ref = SecretRef.parse("prompt")
        assert ref.scheme == "prompt"
        assert ref.params == ("secret",)

    def test_parse_prompt_with_message(self) -> None:
        ref = SecretRef.parse("prompt:Google API key")
        assert ref.scheme == "prompt"
        assert ref.params == ("Google API key",)

    def test_parse_literal(self) -> None:
        ref = SecretRef.parse("literal:my-secret-value")
        assert ref.scheme == "literal"
        assert ref.params == ("my-secret-value",)

    def test_parse_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid secret reference"):
            SecretRef.parse("unknown-format")

    def test_equality(self) -> None:
        a = SecretRef.parse("env:FOO")
        b = SecretRef.parse("env:FOO")
        assert a == b

    def test_hash(self) -> None:
        a = SecretRef.parse("env:FOO")
        b = SecretRef.parse("env:FOO")
        assert hash(a) == hash(b)

    def test_pydantic_auto_parse(self) -> None:
        """SecretRef should auto-parse from string in Pydantic models."""
        provider = ProviderConfig(
            name="test",
            api_key="env:MY_KEY",  # type: ignore[arg-type]
        )
        assert isinstance(provider.api_key, SecretRef)
        assert provider.api_key.scheme == "env"


class TestProviderConfig:
    def test_literal_rejected_on_api_key(self) -> None:
        with pytest.raises(ValidationError, match="literal:"):
            ProviderConfig(
                name="test",
                api_key="literal:my-secret",  # type: ignore[arg-type]
            )

    def test_non_secret_fields_accept_literal(self) -> None:
        """Fields not in _secret_fields should accept any value."""
        provider = ProviderConfig(name="test", base_url="https://api.example.com")
        assert provider.base_url == "https://api.example.com"

    def test_name_defaults_empty(self) -> None:
        provider = ProviderConfig()
        assert provider.name == ""

    def test_extra_body_defaults_empty(self) -> None:
        provider = ProviderConfig(name="test")
        assert provider.extra_body == {}

    def test_extra_body_accepts_arbitrary_dict(self) -> None:
        provider = ProviderConfig(
            name="openrouter",
            extra_body={
                "provider": {"order": ["anthropic"], "allow_fallbacks": False},
                "transforms": [],
            },
        )
        assert provider.extra_body["provider"]["order"] == ["anthropic"]
        assert provider.extra_body["transforms"] == []


class TestModelConfig:
    def test_valid_model(self) -> None:
        model = ModelConfig(**minimal_model_dict())
        assert model.id == "test-model"
        assert model.supports_vision is False  # default

    def test_roles_as_set(self) -> None:
        model = ModelConfig(**minimal_model_dict(roles=["primary", "reasoning"]))
        assert model.roles == {ModelRole.PRIMARY, ModelRole.REASONING}

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelConfig(**minimal_model_dict(roles=["nonexistent"]))


class TestProfileConfig:
    def test_path_expansion_tilde(self, tmp_path: object) -> None:
        profile = ProfileConfig(
            **minimal_profile_dict(
                soul_document_path="~/soul.md",
                user_context_path="~/user.md",
                memory_md_path="~/MEMORY.md",
            )
        )
        # ~ should be expanded to an absolute path
        assert not str(profile.soul_document_path).startswith("~")
        assert profile.soul_document_path.is_absolute()

    def test_defaults(self) -> None:
        profile = ProfileConfig(**minimal_profile_dict())
        assert profile.memory_space == "companion"
        assert profile.budgets.per_turn_usd == 0.50
        assert profile.convention_files.enabled is True


class TestMemoryConfig:
    def test_explicit_remember_importance_default(self) -> None:
        m = MemoryConfig()
        assert m.explicit_remember_importance == 7

    def test_explicit_remember_importance_accepts_bounds(self) -> None:
        assert MemoryConfig(explicit_remember_importance=1).explicit_remember_importance == 1
        assert MemoryConfig(explicit_remember_importance=10).explicit_remember_importance == 10

    def test_explicit_remember_importance_rejects_out_of_bounds(self) -> None:
        with pytest.raises(ValidationError):
            MemoryConfig(explicit_remember_importance=0)
        with pytest.raises(ValidationError):
            MemoryConfig(explicit_remember_importance=11)


class TestBudgetConfig:
    def test_defaults(self) -> None:
        b = BudgetConfig()
        assert b.per_turn_usd == 0.50
        assert b.per_session_usd == 5.00
        assert b.daily_usd == 20.00


class TestCairnConfig:
    def test_valid_full_config(self) -> None:
        config = CairnConfig(**full_config_dict())
        assert config.schema_version == 1
        assert config.active_profile == "personal"
        assert len(config.models) == 2
        assert "personal" in config.profiles

    def test_active_property(self) -> None:
        config = CairnConfig(**full_config_dict())
        assert config.active.name == "pendragon"

    def test_missing_schema_version_rejected(self) -> None:
        data = full_config_dict()
        del data["schema_version"]
        with pytest.raises(ValidationError):
            CairnConfig(**data)

    def test_invalid_schema_version_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            CairnConfig(**full_config_dict(schema_version=0))

    def test_active_profile_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="not found"):
            CairnConfig(**full_config_dict(active_profile="nonexistent"))

    def test_frozen(self) -> None:
        config = CairnConfig(**full_config_dict())
        with pytest.raises(ValidationError):
            config.active_profile = "work"  # type: ignore[misc]
