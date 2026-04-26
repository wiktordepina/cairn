"""Tests for ModelRegistry."""

from __future__ import annotations

import pytest

from cairn.config._models import ModelConfig, ModelRole
from cairn.config._registry import AmbiguousRoleError, ModelNotFoundError, ModelRegistry

from .conftest import minimal_model_dict


def _model(id: str, roles: list[str] | None = None, **kw: object) -> ModelConfig:
    return ModelConfig(**minimal_model_dict(id=id, roles=roles or [], **kw))


class TestModelRegistry:
    def test_models_preserves_declaration_order(self) -> None:
        registry = ModelRegistry([_model("opus"), _model("haiku"), _model("gpt-5")])
        ids = [m.id for m in registry.models()]
        assert ids == ["opus", "haiku", "gpt-5"]

    def test_by_id(self) -> None:
        registry = ModelRegistry([_model("opus"), _model("haiku")])
        assert registry.by_id("opus").id == "opus"

    def test_by_id_missing(self) -> None:
        registry = ModelRegistry([_model("opus")])
        with pytest.raises(ModelNotFoundError, match="sonnet"):
            registry.by_id("sonnet")

    def test_by_role(self) -> None:
        registry = ModelRegistry([_model("opus", roles=["primary"])])
        assert registry.by_role(ModelRole.PRIMARY).id == "opus"

    def test_by_role_missing(self) -> None:
        registry = ModelRegistry([_model("opus", roles=["primary"])])
        with pytest.raises(ModelNotFoundError, match="utility"):
            registry.by_role(ModelRole.UTILITY)

    def test_resolve_by_id(self) -> None:
        registry = ModelRegistry([_model("opus")])
        assert registry.resolve("opus").id == "opus"

    def test_resolve_by_role(self) -> None:
        registry = ModelRegistry([_model("opus", roles=["primary"])])
        assert registry.resolve("role:primary").id == "opus"

    def test_resolve_invalid_role(self) -> None:
        registry = ModelRegistry([_model("opus")])
        with pytest.raises(ModelNotFoundError, match="Unknown model role"):
            registry.resolve("role:nonexistent")

    def test_ambiguous_role_rejected(self) -> None:
        with pytest.raises(AmbiguousRoleError, match="primary"):
            ModelRegistry(
                [
                    _model("opus", roles=["primary"]),
                    _model("sonnet", roles=["primary"]),
                ]
            )

    def test_duplicate_id_rejected(self) -> None:
        with pytest.raises(AmbiguousRoleError, match="Duplicate model ID"):
            ModelRegistry([_model("opus"), _model("opus")])

    def test_model_with_multiple_roles(self) -> None:
        registry = ModelRegistry(
            [
                _model("opus", roles=["primary", "reasoning"]),
                _model("haiku", roles=["utility", "fast"]),
            ]
        )
        assert registry.resolve("role:primary").id == "opus"
        assert registry.resolve("role:reasoning").id == "opus"
        assert registry.resolve("role:utility").id == "haiku"
        assert registry.resolve("role:fast").id == "haiku"
