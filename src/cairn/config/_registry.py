"""Model registry — resolve model references by ID or role."""

from __future__ import annotations

from cairn.config._models import ModelConfig, ModelRole


class ModelNotFoundError(Exception):
    """Raised when a model cannot be found by ID or role."""


class AmbiguousRoleError(Exception):
    """Raised when multiple models claim the same role."""


class ModelRegistry:
    """Registry of available models, constructed from config.

    Provides lookup by model ID and by role, plus a ``resolve()`` method
    that accepts either ``"claude-opus-4-7"`` (ID) or ``"role:primary"``
    (role reference).
    """

    def __init__(self, models: list[ModelConfig]) -> None:
        self._by_id: dict[str, ModelConfig] = {}
        self._by_role: dict[ModelRole, ModelConfig] = {}

        for model in models:
            if model.id in self._by_id:
                raise AmbiguousRoleError(f"Duplicate model ID: {model.id!r}")
            self._by_id[model.id] = model

            for role in model.roles:
                if role in self._by_role:
                    existing = self._by_role[role]
                    raise AmbiguousRoleError(
                        f"Role {role.value!r} is claimed by both "
                        f"{existing.id!r} and {model.id!r}. "
                        f"Each role must map to exactly one model."
                    )
                self._by_role[role] = model

    def by_id(self, model_id: str) -> ModelConfig:
        """Look up a model by its ID. Raises ``ModelNotFoundError`` on miss."""
        try:
            return self._by_id[model_id]
        except KeyError:
            raise ModelNotFoundError(
                f"No model with ID {model_id!r}. Available: {sorted(self._by_id.keys())}"
            ) from None

    def by_role(self, role: ModelRole) -> ModelConfig:
        """Look up a model by role. Raises ``ModelNotFoundError`` on miss."""
        try:
            return self._by_role[role]
        except KeyError:
            raise ModelNotFoundError(
                f"No model with role {role.value!r}. "
                f"Available roles: {sorted(r.value for r in self._by_role)}"
            ) from None

    def resolve(self, ref: str) -> ModelConfig:
        """Resolve a model reference — either a direct ID or ``role:<name>``.

        Examples::

            registry.resolve("claude-opus-4-7")   # by ID
            registry.resolve("role:primary")       # by role
        """
        if ref.startswith("role:"):
            role_name = ref[5:]
            try:
                role = ModelRole(role_name)
            except ValueError:
                raise ModelNotFoundError(
                    f"Unknown model role: {role_name!r}. "
                    f"Valid roles: {[r.value for r in ModelRole]}"
                ) from None
            return self.by_role(role)
        return self.by_id(ref)
