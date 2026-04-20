"""Tests for OrchestratorConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.orchestrator import OrchestratorConfig


class TestOrchestratorConfig:
    def test_defaults(self) -> None:
        cfg = OrchestratorConfig()
        assert cfg.max_iterations == 10
        assert cfg.max_model_behaviour_retries == 2
        assert cfg.max_turn_duration_s == 600.0
        assert cfg.preparer_timeout_s == 30.0
        assert cfg.event_queue_size == 64
        assert cfg.budget_warn_threshold_fraction == 0.8

    def test_custom_values(self) -> None:
        cfg = OrchestratorConfig(
            max_iterations=5,
            max_turn_duration_s=60.0,
            event_queue_size=32,
        )
        assert cfg.max_iterations == 5
        assert cfg.max_turn_duration_s == 60.0
        assert cfg.event_queue_size == 32

    def test_frozen(self) -> None:
        cfg = OrchestratorConfig()
        with pytest.raises(ValidationError):
            cfg.max_iterations = 5  # type: ignore[misc]

    def test_rejects_non_positive_iterations(self) -> None:
        with pytest.raises(ValidationError):
            OrchestratorConfig(max_iterations=0)

    def test_rejects_zero_turn_duration(self) -> None:
        with pytest.raises(ValidationError):
            OrchestratorConfig(max_turn_duration_s=0)

    def test_rejects_negative_retries(self) -> None:
        with pytest.raises(ValidationError):
            OrchestratorConfig(max_model_behaviour_retries=-1)

    def test_rejects_invalid_warn_fraction(self) -> None:
        with pytest.raises(ValidationError):
            OrchestratorConfig(budget_warn_threshold_fraction=0)
        with pytest.raises(ValidationError):
            OrchestratorConfig(budget_warn_threshold_fraction=1.5)
