"""Unit tests for `cairn.ui._model_label.resolve_label`."""

from __future__ import annotations

from cairn.config._models import ModelConfig
from cairn.config._registry import ModelRegistry
from cairn.ui._model_label import resolve_label


def _model(model_id: str, display_name: str) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        provider="anthropic",
        display_name=display_name,
        context_window=200_000,
        max_output_tokens=8_000,
        supports_tools=True,
        input_cost_per_1m=1.0,
        output_cost_per_1m=5.0,
    )


class TestResolveLabel:
    def test_returns_display_name_when_registered(self) -> None:
        registry = ModelRegistry([_model("opus-4-7", "Claude Opus 4.7")])
        assert resolve_label(registry, "opus-4-7") == "Claude Opus 4.7"

    def test_falls_back_to_id_when_registry_is_none(self) -> None:
        # Test harness path: no registry plumbed.
        assert resolve_label(None, "opus-4-7") == "opus-4-7"

    def test_falls_back_to_id_when_model_not_found(self) -> None:
        # Config dropped this model mid-session — keep rendering the
        # bare id rather than raising.
        registry = ModelRegistry([_model("opus-4-7", "Claude Opus 4.7")])
        assert resolve_label(registry, "haiku-4-5") == "haiku-4-5"
