"""Pydantic models for cairn configuration."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ModelRole(StrEnum):
    """Roles a model can fulfil within a profile."""

    PRIMARY = "primary"
    UTILITY = "utility"
    EXTRACTION = "extraction"
    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    FAST = "fast"


# ---------------------------------------------------------------------------
# SecretRef — a reference to a secret, never the value itself
# ---------------------------------------------------------------------------


class SecretRef:
    """A reference to a secret value — never the value itself.

    Parsed from strings like `keyring:cairn:anthropic-api-key`,
    `env:OPENAI_API_KEY`, `prompt:Google API key`, or `literal:value`.
    """

    __slots__ = ("scheme", "params")

    def __init__(self, scheme: str, params: tuple[str, ...]) -> None:
        self.scheme = scheme
        self.params = params

    @classmethod
    def parse(cls, raw: str) -> SecretRef:
        """Parse a secret reference string into a `SecretRef`."""
        match raw.split(":", 2):
            case ["keyring", service, key]:
                return cls(scheme="keyring", params=(service, key))
            case ["env", name]:
                return cls(scheme="env", params=(name,))
            case ["prompt"]:
                return cls(scheme="prompt", params=("secret",))
            case ["prompt", message]:
                return cls(scheme="prompt", params=(message,))
            case ["literal", value]:
                return cls(scheme="literal", params=(value,))
            case _:
                raise ValueError(f"Invalid secret reference: {raw!r}")

    def __repr__(self) -> str:
        return f"SecretRef(scheme={self.scheme!r}, params={self.params!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretRef):
            return NotImplemented
        return self.scheme == other.scheme and self.params == other.params

    def __hash__(self) -> int:
        return hash((self.scheme, self.params))

    # Pydantic integration ------------------------------------------------

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            cls._pydantic_validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._pydantic_serialize,
                info_arg=False,
            ),
        )

    @classmethod
    def _pydantic_validate(cls, value: Any) -> SecretRef:
        if isinstance(value, SecretRef):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise ValueError(f"Expected a string or SecretRef, got {type(value).__name__}")

    @staticmethod
    def _pydantic_serialize(value: SecretRef) -> str:
        return f"{value.scheme}:{':'.join(value.params)}"


# ---------------------------------------------------------------------------
# Helper: expand paths (~ and env vars)
# ---------------------------------------------------------------------------


def _expand_path(v: Any) -> Path:
    """Expand `~` and environment variables in a path value."""
    if isinstance(v, str):
        v = Path(v)
    if isinstance(v, Path):
        return Path(os.path.expandvars(str(v.expanduser())))
    raise ValueError(f"Expected a string or Path, got {type(v).__name__}")


ExpandedPath = Annotated[Path, BeforeValidator(_expand_path)]


# ---------------------------------------------------------------------------
# Config models — frozen (immutable once loaded)
# ---------------------------------------------------------------------------

CURRENT_SCHEMA_VERSION = 1


class BudgetConfig(BaseModel):
    """Cost caps per turn, session, and day."""

    model_config = ConfigDict(frozen=True)

    per_turn_usd: float = 0.50
    per_session_usd: float = 5.00
    daily_usd: float = 20.00


class ConventionFilesConfig(BaseModel):
    """Settings for loading project convention files (AGENTS.md, etc.)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    filenames: list[str] = ["CAIRN.md", "AGENTS.md", "CLAUDE.md"]
    walk_up_to: Literal["git_root", "filesystem_root", "cwd_only"] = "git_root"
    search_subdirs: bool = True
    max_bytes_per_file: int = 65_536
    trust_policy: Literal["prompt", "always", "project_allowlist"] = "prompt"
    user_level_paths: list[str] = []


class MemoryConfig(BaseModel):
    """Tunables for the memory brick.

    All knobs live on `ProfileConfig.memory`. Defaults are the shipped
    V1 values — changing them is a profile-level override, not a
    system-wide one.
    """

    model_config = ConfigDict(frozen=True)

    # Extraction ---------------------------------------------------------
    extraction_context_turns: int = 3
    """Number of prior turns to include as context when extracting from
    the latest turn. Helps disambiguate short replies ("yes, do that")
    without re-extracting old material."""

    min_extraction_chars: int = 200
    """Skip extraction when the latest turn's combined user + assistant
    text is below this length. Avoids spending utility-model calls on
    "thanks!" / "ok" / "yes that works" turns."""

    extract_from_personas: bool = True
    """If False, persona sessions don't trigger extraction. Keep True
    unless a persona is intentionally stateless."""

    extract_from_tool_only_turns: bool = True
    """If True, extract even from turns where the assistant produced no
    user-facing prose but did invoke tools. The length gate still
    applies to the combined transcript."""

    max_extraction_cost_usd: float = 0.01
    """Per-extraction cost cap. Mid-stream responses truncating past
    this limit still record whatever was parsed so far."""

    max_pending_extractions: int = 100
    """Hard cap on queued extraction jobs. Overflows drop the oldest
    queued job and log a WARNING."""

    # Retrieval ----------------------------------------------------------
    retrieval_k: int = 8
    """Top-k memories surfaced per turn."""

    retrieval_content_truncate: int = 200
    """Per-entry content truncation when rendering retrieved memories
    into the system prompt."""

    # Observation log ----------------------------------------------------
    observation_log_fsync: bool = False
    """Fsync each JSONL append. Default off — SQLite is the durability
    store; losing the last few lines of JSONL on crash is acceptable."""


class CompactionConfig(BaseModel):
    """Tunables for conversation-history compaction.

    All knobs live on `ProfileConfig.compaction`; each persona is its
    own profile, so overrides are per-persona by construction.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    """Master kill switch. `False` disables the preparer entirely —
    history is never truncated. Useful when debugging
    context-loss bugs."""

    preserve_last_n_turns: int = 6
    """Hard floor on surviving turn blocks. Default 6 covers ~3
    user/assistant round trips. Tool-heavy personas may want to
    raise this."""

    safety_margin_tokens: int = 2048
    """Tokens held back from `context_window` on top of
    `request.max_tokens` to cover tokenizer drift and
    retrieved-memory slop."""

    min_history_tokens: int = 1024
    """If `context_window - max_tokens - safety_margin` falls below
    this, the profile is misconfigured — log ERROR and pass the
    request through unchanged."""


class DelegationToolConfig(BaseModel):
    """Configuration for a delegation tool (consult another model)."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    target_model: str
    description: str
    when_to_use: str
    preserve_history: bool = False
    sub_system_prompt: str | None = None
    max_cost_usd: float | None = None
    approval_required: bool = False


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider (Anthropic, OpenAI, etc.)."""

    model_config = ConfigDict(frozen=True)

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"api_key"})

    name: str = ""  # populated from dict key during loading
    api_key: SecretRef | None = None
    base_url: str | None = None
    extra_headers: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def reject_literal_secrets(cls, data: Any) -> Any:  # noqa: ANN401
        """Reject `literal:` scheme on fields marked as secret."""
        if not isinstance(data, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            return data
        typed_data: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
        for field_name in cls._secret_fields:
            ref = typed_data.get(field_name)
            if isinstance(ref, str) and ref.startswith("literal:"):
                raise ValueError(
                    f"'{field_name}' must not use the literal: scheme — "
                    f"use keyring:, env:, or prompt: instead"
                )
        return typed_data


class ModelConfig(BaseModel):
    """Configuration for a single model."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_prompt_cache: bool = False
    input_cost_per_1m: float
    output_cost_per_1m: float
    cache_read_cost_per_1m: float | None = None
    cache_write_cost_per_1m: float | None = None
    roles: set[ModelRole] = set()


class ProfileConfig(BaseModel):
    """Configuration for a named profile (companion, work, etc.)."""

    model_config = ConfigDict(frozen=True)

    name: str | None = None  # elicited on first run if None
    soul_document_path: ExpandedPath
    user_context_path: ExpandedPath
    memory_md_path: ExpandedPath
    memory_space: str = "companion"
    primary_model: str  # model ID or "role:<name>"
    utility_model: str
    delegation_tools: list[DelegationToolConfig] = []
    budgets: BudgetConfig = BudgetConfig()
    convention_files: ConventionFilesConfig = ConventionFilesConfig()
    memory: MemoryConfig = MemoryConfig()
    compaction: CompactionConfig = CompactionConfig()


class CairnConfig(BaseModel):
    """Top-level cairn configuration, assembled from merged TOML layers."""

    model_config = ConfigDict(frozen=True)

    schema_version: int
    active_profile: str
    providers: dict[str, ProviderConfig] = {}
    models: list[ModelConfig] = []
    profiles: dict[str, ProfileConfig] = {}

    @field_validator("schema_version")
    @classmethod
    def check_schema_version(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"schema_version must be >= 1, got {v}")
        return v

    @model_validator(mode="after")
    def check_active_profile_exists(self) -> Self:
        if self.profiles and self.active_profile not in self.profiles:
            raise ValueError(
                f"active_profile {self.active_profile!r} not found in profiles: "
                f"{sorted(self.profiles.keys())}"
            )
        return self

    @property
    def active(self) -> ProfileConfig:
        """Return the active profile's configuration."""
        return self.profiles[self.active_profile]
