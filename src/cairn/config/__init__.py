"""Cairn configuration — loading, merging, validation, and secret resolution."""

from cairn.config._loader import ConfigError, config_paths, load_config
from cairn.config._merge import deep_merge
from cairn.config._migrations import MigrationError, migrate, migration
from cairn.config._models import (
    CURRENT_SCHEMA_VERSION,
    BudgetConfig,
    CairnConfig,
    ConventionFilesConfig,
    DelegationToolConfig,
    MemoryConfig,
    ModelConfig,
    ModelRole,
    ProfileConfig,
    ProviderConfig,
    SecretRef,
)
from cairn.config._registry import AmbiguousRoleError, ModelNotFoundError, ModelRegistry
from cairn.config._secrets import MissingSecretError, SecretResolver

__all__ = [
    # Models
    "BudgetConfig",
    "CairnConfig",
    "ConventionFilesConfig",
    "CURRENT_SCHEMA_VERSION",
    "DelegationToolConfig",
    "MemoryConfig",
    "ModelConfig",
    "ModelRole",
    "ProfileConfig",
    "ProviderConfig",
    "SecretRef",
    # Loading
    "ConfigError",
    "config_paths",
    "load_config",
    # Merge
    "deep_merge",
    # Migrations
    "MigrationError",
    "migrate",
    "migration",
    # Registry
    "AmbiguousRoleError",
    "ModelNotFoundError",
    "ModelRegistry",
    # Secrets
    "MissingSecretError",
    "SecretResolver",
]
