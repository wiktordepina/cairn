## Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is pre-1.0, minor version bumps track meaningful additions
of functionality (each "brick" in `.plan/implementation-progress.md`) and
the public surface is still in flux.

## [Unreleased]

Nothing yet. Next up: the orchestrator turn loop.

## [0.3.0] — 2026-04-20

### Added — Persistence (`src/cairn/persistence/`)

Per-profile SQLite persistence layer. Architecture doc §6.

- Per-profile database at `$XDG_DATA_HOME/cairn/<profile>/cairn.db`, with
  the parent directory created at `0o700`.
- Hand-rolled numbered SQL migrations in `_sql/` with a `schema_migrations`
  tracking table, transactional apply, gap detection, and rejection of
  "DB ahead of code" states. Migrations are loaded via
  `importlib.resources` so they survive packaging.
- `Database` class — lazy `aiosqlite` handle with sensible PRAGMAs
  (WAL, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`,
  `temp_store=MEMORY`) and a `transaction()` context manager that wraps
  `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`.
- Four core tables with appropriate indexes:
  - `sessions` — `Session` entity.
  - `messages` — `content_json TEXT`, `UNIQUE(session_id, idx)`, FK CASCADE.
  - `tool_calls` — full lifecycle plus delegation linkage.
  - `model_usage` — provider-call records. Intentionally *no* FK CASCADE,
    so usage rows survive session deletion for cost auditing.
- Four repositories:
  - `SessionRepo` — `insert`, `get`, `update_metadata` (auto-bumps
    `updated_at`), `list_for_space` (with explicit `None` for memoryless
    sessions), `list_recent` (deliberately cross-space), `children_of`,
    `archive`.
  - `MessageRepo` — `append` (server-side `idx` assignment under
    `BEGIN IMMEDIATE`), `insert_with_idx` for replay/import, `get`,
    `list_for_session`, `count`, `replace_content`.
  - `ToolCallRepo` — lifecycle transitions enforced via conditional
    `UPDATE` (raises `InvalidToolCallTransition` on bad state). Methods:
    `start`, `approve`, `reject`, `mark_executing`, `complete` (auto-
    computes `duration_ms`), `mark_failed`, `mark_timed_out`,
    `mark_cancelled`, `get`, `list_for_session`, `list_pending_approval`.
  - `UsageRepo` — `record` (one row per provider call, retries tracked
    in `metadata`) plus aggregations: `total_cost_for_session`,
    `total_cost_for_session_tree`, `cost_in_window`, `cost_today_utc`,
    `cost_per_turn`, `by_operation_in_window`, `by_model_in_window`,
    `list_recent`.
- Memory-space scoping enforced at the repo layer: `list_for_space`
  requires an explicit space, and cross-space methods are explicitly
  named.
- Frozen Pydantic records: `ToolCallRecord`, `UsageRecord`.
- Profile-name sanitisation (`_sanitise()`): only alphanumerics, dash,
  and underscore; rejects path separators.

### Tests

- 61 new tests across `tests/persistence/`.
- **Total suite: 284 passing** (61 persistence + 72 providers + 77 domain
  + 74 config).

### Deferred

- `MemoryRepo`, FTS5 shadow table, vector tables — memory brick.
- Connection pooling, bulk-insert paths, Python-based migrations,
  SQLCipher encryption-at-rest, `wal_autocheckpoint` tuning, soft-delete
  policy beyond `archived`.

Commit: [`9c2f3bf`](https://github.com/wiktordepina/cairn/commit/9c2f3bf9ff739a1726b9db0d5f3e5a0a07b38aac).

## [0.2.0] — 2026-04-19

### Added — Domain types (`src/cairn/domain/`)

Architecture doc §4.4, §4.5, parts of §4.3 and §4.7.

- **Content blocks** (frozen Pydantic, discriminated union via a `type`
  literal): `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ImageBlock`
  (plus `ImageSource`), `ThinkingBlock`. `ContentBlock` union and a
  `content_list_adapter` (`TypeAdapter`) give clean JSON round-trips to
  and from the `content_json` DB column.
- **`Message`** — plain class with `__slots__`, mutable during streaming.
  Methods: `append_text_delta`, `start_tool_use` /
  `append_tool_input_delta` / `finalize_tool_use`, `get_tool_use`,
  `get_text`, `content_json()`, `from_content_json()`. Auto-generates
  UUID and `created_at` when not provided.
- **`Session`** — frozen Pydantic. Fields: `id`, `type`, `persona`,
  `model`, `memory_space`, `title`, `created_at`, `updated_at`,
  `archived`, `parent_session_id`.
- **Enums** (all `StrEnum`): `SessionType` (companion / persona /
  ephemeral), `StopReason` (end_turn / max_tokens / tool_use),
  `UsageOperation` (primary_turn, delegation, compaction, extraction,
  reflection, titling, embedding), `ToolCallStatus` (pending, approved,
  rejected, executing, completed, failed, timed_out, cancelled),
  `ErrorClass` (transient / llm / user / unexpected), `MemoryEntryType`
  (fact, preference, insight, relationship, event, task), `MemoryClass`
  (semantic / episodic).
- **Provider request / event types** (frozen dataclasses):
  `ProviderRequest`, `ToolDefinition`, `TextDelta`, `ToolCallStart`,
  `ToolCallDelta`, `ToolCallEnd`, `UsageEvent`, `MessageStop`, and the
  `ProviderEvent` union.
- **UI events** (frozen dataclasses): `UserMessagePersisted`,
  `AssistantTextDelta`, `AssistantMessageComplete`, `ToolCallStarted`,
  `ToolCallCompleted`, `DelegationSpawned`, `DelegationCompleted`,
  `ObservationExtractionRequested`, `TurnComplete`, `SessionCreated`,
  `SessionResumed`, `SessionArchived`, plus the `UIEvent` union.

### Added — Providers (`src/cairn/providers/`)

Architecture doc §4.3. Bundled in the same commit as domain types.

- **`Provider` protocol** (`@runtime_checkable`): `name` property,
  `stream(request) -> AsyncIterator[ProviderEvent]`,
  `count_tokens(request) -> int`.
- **Error hierarchy**: `ProviderError(provider, retryable)`,
  `AuthenticationError`, `RateLimitError(retry_after)`,
  `ModelNotAvailableError`, `ProviderOverloadedError`.
- **Three concrete adapters**, each lazy-initialising the SDK client and
  lazy-resolving secrets:
  - `AnthropicProvider` — uses
    `anthropic.AsyncAnthropic.messages.stream()`; maps SDK events to
    `ProviderEvent`. `count_tokens` uses the native SDK endpoint.
  - `OpenAIProvider` — uses
    `openai.AsyncOpenAI.chat.completions.create(stream=True)` with tool-
    call index → ID tracking. Also serves OpenAI-compatible local
    servers (llama.cpp, vLLM, LM Studio) via `base_url`. `count_tokens`
    uses `tiktoken`, with a character-estimate fallback for unknown
    models.
  - `OpenRouterProvider` — same `openai` SDK underneath but a separate
    adapter for OR-specific identity headers (`HTTP-Referer`,
    `X-Title`) and error semantics. Reuses `format_messages` /
    `format_tools` from `_openai.py`.
- **`ProviderRegistry`** — factory + cache. `for_model(model_config)`
  dispatches via `by_name(model_config.provider)`. `register_adapter()`
  for future providers.
- **Translation helpers** (`_translate.py`): `extract_text`,
  `has_tool_uses`, `get_tool_uses`, `get_tool_results`, `get_images`,
  `get_thinking`, `map_anthropic_stop_reason`, `map_openai_stop_reason`.
- Per-provider message-format translation:
  - Anthropic — separate `system` parameter, inline `tool_use` blocks,
    `role:user` tool-result messages, native `image` source.
  - OpenAI / OpenRouter — prepended `role:system` message, `tool_calls`
    array on assistant messages, separate `role:tool` result messages,
    `image_url` data URLs, thinking blocks stripped on the way out.
- End-to-end verified against real OpenAI and OpenRouter APIs (text
  streaming and tool calling).

### Tests

- 77 new tests in `tests/domain/`.
- 72 new tests in `tests/providers/`.

### Deferred

- Domain: full `MemoryEntry` model, concrete exception hierarchy,
  message-compaction types.
- Providers: Google (`google-genai`) adapter, OpenRouter fallback
  chains, prompt-cache markers in `ProviderRequest`, retry loop,
  `supports_thinking` gating, non-base64 image uploads.

Commit: [`6c71524`](https://github.com/wiktordepina/cairn/commit/6c7152400c423b501315bb267c7ea78c6d33bb57).

## [0.1.0] — 2026-04-19

### Added — Project skeleton

- `pyproject.toml` with hatchling build backend, Python ≥ 3.13,
  runtime dependencies (`pydantic`, `platformdirs`, `keyring`,
  `watchfiles`, `anthropic`, `openai`, `tiktoken`, `aiosqlite`), dev
  group (`pytest`, `pytest-asyncio`, `ruff`, `pyright`), and `cairn`
  console script wired to `cairn.cli:main`.
- `src/cairn/` package layout with `py.typed` marker.
- Ruff config targeting `py313` with a 99-column line length and a
  pragmatic lint selection (`E`, `F`, `W`, `I`, `UP`, `B`, `SIM`,
  `TCH`).
- Strict Pyright config.
- README describing the companion-first, memory-first,
  model-agnostic design.

### Added — Config (`src/cairn/config/`)

Architecture doc §4.1.

- Schema-versioned TOML: `schema_version = 1` is required; future
  versions warn rather than hard-fail.
- Three-tier merge: user
  (`$XDG_CONFIG_HOME/cairn/config.toml`) + project
  (`<repo>/.cairn/config.toml`) + local
  (`<repo>/.cairn/config.local.toml`).
- Project-root discovery: walk up to the git root looking for
  `.cairn/config.toml`.
- Profile resolution order: CLI `--profile` > local > project > user.
- Pydantic models, all frozen: `CairnConfig`, `ProfileConfig`,
  `ProviderConfig`, `ModelConfig`, `BudgetConfig`,
  `DelegationToolConfig`, `ConventionFilesConfig`, `ModelRole` enum.
- `SecretRef` with four schemes: `keyring:`, `env:`, `prompt:`,
  `literal:`. `literal:` is rejected on fields marked as secrets.
- `SecretResolver` — lazy resolution, prompt-cached for the process.
- `ModelRegistry`: `by_id()`, `by_role()`, `resolve("role:primary")`.
- Migration infrastructure: `@migration` decorator, chain runner, gap
  detection.
- `ProviderConfig.name` injected from the TOML dict key during load.
- Path expansion (`~` and env vars) on profile path fields.

### Tests

- 74 new tests in `tests/config/`.

### Deferred

- File watching (`_watcher.py`), CLI commands
  (`cairn config show/validate/migrate/secret/diff`), auto-migration
  prompts, `config explain`, the `/reload` slash command. All
  blocked on infrastructure not yet in place (event system, CLI
  framework, UI).

Commits: [`85d8e30`](https://github.com/wiktordepina/cairn/commit/85d8e30a4c48981d03cbdb4307b06e2ff29de289),
[`35b1c91`](https://github.com/wiktordepina/cairn/commit/35b1c911c039754389c296b67f3f1c5dc16bc4f9).

[Unreleased]: https://github.com/wiktordepina/cairn/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/wiktordepina/cairn/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/wiktordepina/cairn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/wiktordepina/cairn/releases/tag/v0.1.0
