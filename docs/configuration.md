# Configuration

Cairn is configured via TOML. This page is the reference for every key
you can set.

Philosophy:

- **Schema-versioned.** The config carries `schema_version`; mismatched
  versions either warn or error rather than misbehave silently.
- **Three-tier merge.** User defaults + project overrides + local
  personal tweaks, resolved deterministically.
- **Secret-aware.** API keys are `SecretRef`s — references to where the
  secret *lives* (keychain, env var, prompt), never the value itself.
- **Frozen at load time.** The merged config is immutable for the
  lifetime of the process. File edits are picked up explicitly, not
  silently.

## File locations and discovery order

Cairn reads and merges up to three files:

| Tier | Path | Intended for |
|---|---|---|
| **User** | `$XDG_CONFIG_HOME/cairn/config.toml` (defaults to `~/.config/cairn/config.toml`) | Your personal defaults |
| **Project** | `<repo>/.cairn/config.toml` | Team-level overrides, checked in |
| **Local** | `<repo>/.cairn/config.local.toml` | Personal per-project tweaks, gitignored |

Project-root discovery walks up from the current working directory
looking for a `.cairn/config.toml`, stopping at the git root by
default.

**Merge order** (later tiers override earlier):

```
user  <  project  <  local  <  CLI flags
```

## Profile resolution

A profile bundles everything that defines *one way to work*: which model,
which soul document, which memory space, which budgets. You can define
multiple profiles (e.g. `companion`, `work`, `experimental`) and switch
between them.

**Active profile resolution order** (highest priority wins):

1. `--profile <name>` CLI flag
2. `active_profile` set in the local config
3. `active_profile` set in the project config
4. `active_profile` set in the user config

If no profile is named, cairn elicits one on first run.

## Top-level keys

```toml
schema_version = 1        # required; reject on mismatch
active_profile = "companion"
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `schema_version` | int | — (required) | Must be `1` today. Future versions will warn. |
| `active_profile` | string | — | Must match a key in `[profiles.*]`. |

## `[providers.<name>]`

A provider is a vendor you call out to. Each provider has one config
block. The block's key is the provider's name — `anthropic`, `openai`,
`openrouter`, etc.

```toml
[providers.anthropic]
api_key = "keyring:cairn:anthropic-api-key"

[providers.openai]
api_key = "env:OPENAI_API_KEY"

[providers.openrouter]
api_key = "keyring:cairn:openrouter-api-key"
extra_headers = { "HTTP-Referer" = "https://github.com/you/your-project" }

[providers.llamacpp]
api_key = "literal:not-required"   # ← rejected, see below
base_url = "http://localhost:8080/v1"
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `api_key` | [`SecretRef`](#secret-references) | — | Must not use the `literal:` scheme (rejected at load time). |
| `base_url` | string | — | Override for OpenAI-compatible local servers. See [providers.md](providers.md#openai-compatible-local-servers). |
| `extra_headers` | `{ string: string }` | `{}` | Merged into every request. Used for OpenRouter identity headers. |
| `extra_body` | `{ string: any }` | `{}` | Opaque pass-through merged into the request body. Currently consumed by the OpenRouter adapter for `provider.*` routing preferences (see below). Adapter-set kwargs always win — you cannot clobber `stream`, `model`, `messages`. |

Provider names are injected into the `ProviderConfig` from the TOML dict
key, so you don't repeat them inside the block.

### `extra_body` for OpenRouter

OpenRouter's request body accepts a `provider` object that controls
upstream routing. With `extra_body` you can pin to specific upstreams,
allow / disable fallbacks, opt into zero-data-retention routing, sort
by price / latency / throughput, and more — without an adapter
release. Example:

```toml
[providers.openrouter]
api_key = "keyring:cairn:openrouter-api-key"
extra_body = {
    provider = {
        order = ["anthropic", "google"],   # try Anthropic first, then Google
        allow_fallbacks = false,           # don't fall back to anyone else
        zdr = true,                        # zero-data-retention upstreams only
        sort = "throughput",               # tie-break by speed
    }
}
```

See [OpenRouter's provider-routing docs](https://openrouter.ai/docs/features/provider-routing)
for the full schema. cairn does not validate `extra_body` contents
beyond passing them through.

## `[[models]]`

A model is a specific thing you call — a vendor model ID plus cost and
capability metadata. Define one `[[models]]` block per model you want to
use.

```toml
[[models]]
id = "claude-sonnet-4-6"
provider = "anthropic"
display_name = "Claude Sonnet 4.6"
context_window = 200_000
max_output_tokens = 8_192
supports_tools = true
supports_vision = true
supports_prompt_cache = true
input_cost_per_1m = 3.00
output_cost_per_1m = 15.00
cache_read_cost_per_1m = 0.30
cache_write_cost_per_1m = 3.75
roles = ["primary"]
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `id` | string | — (required) | Vendor model ID. |
| `provider` | string | — (required) | Key into `[providers.*]`. |
| `display_name` | string | — (required) | Shown in the UI. |
| `context_window` | int | — (required) | Max input tokens. |
| `max_output_tokens` | int | — (required) | Max tokens per response. |
| `supports_tools` | bool | — (required) | Whether the model can call tools. |
| `supports_vision` | bool | `false` | Whether the model accepts images. |
| `supports_thinking` | bool | `false` | Whether the model has extended-thinking mode. |
| `supports_prompt_cache` | bool | `false` | When true, cairn emits cache markers (Anthropic, OpenRouter) or relies on the provider's automatic cache (OpenAI, DeepSeek). See [Prompt caching](prompt-caching.md). |
| `input_cost_per_1m` | float | — (required) | USD per 1M input tokens. |
| `output_cost_per_1m` | float | — (required) | USD per 1M output tokens. |
| `cache_read_cost_per_1m` | float | `null` | USD per 1M tokens read from cache. Required when `supports_prompt_cache=true` for accurate cost reporting. |
| `cache_write_cost_per_1m` | float | `null` | USD per 1M tokens written to cache. Set on Anthropic-backed models; leave `null` for OpenAI / DeepSeek where writes are not separately billed. |
| `roles` | list of [role](#model-roles) | `[]` | Roles this model fulfils. |

### Model roles

Roles let profiles reference models semantically (`role:primary`) rather
than by vendor ID (`claude-opus-4-7`). Swapping models becomes a
config change.

| Role | Purpose |
|---|---|
| `primary` | The companion's voice. Default turn model. |
| `utility` | Cheap, fast model for background work (extraction, titling). |
| `reasoning` | Stronger model consulted via delegation for hard problems. |
| `coding` | Model optimised for code; consulted via delegation. |
| `vision` | Model with image input support. |
| `fast` | Low-latency model for latency-sensitive paths. |

A model can fulfil multiple roles (`roles = ["primary", "vision"]`).
Where a profile specifies `"role:<name>"`, cairn picks the first model
in the registry with that role.

## `[profiles.<name>]`

A profile is one named way to work. The key names the profile.

```toml
[profiles.companion]
soul_document_path = "~/.config/cairn/soul.md"
user_context_path = "~/.config/cairn/user_context.md"
memory_md_path = "~/.config/cairn/MEMORY.md"
memory_space = "companion"
primary_model = "role:primary"
utility_model = "role:utility"

[profiles.companion.budgets]
per_turn_usd = 0.50
per_session_usd = 5.00
daily_usd = 20.00

[profiles.companion.convention_files]
enabled = true
filenames = ["CAIRN.md", "AGENTS.md", "CLAUDE.md"]
walk_up_to = "git_root"
trust_policy = "prompt"

[[profiles.companion.delegation_tools]]
tool_name = "ask_reasoning_model"
target_model = "role:reasoning"
description = "Delegate to a stronger reasoning model."
when_to_use = "Hard logic, multi-step deduction, tight maths."
max_cost_usd = 0.50
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `name` | string | `null` | Display name. Elicited on first run if not set. |
| `soul_document_path` | path | — (required) | Soul document — the companion's identity. |
| `user_context_path` | path | — (required) | User context — facts about you. |
| `memory_md_path` | path | — (required) | Curated MEMORY.md. |
| `memory_space` | string | `"companion"` | Tenant key for memory isolation. |
| `primary_model` | string | — (required) | Model ID or `"role:<name>"`. |
| `utility_model` | string | — (required) | Used for observation extraction + compaction. |
| `delegation_tools` | list | `[]` | See [`DelegationTool`](#delegation-tools). |
| `budgets` | table | defaults | See [`budgets`](#budgets). |
| `convention_files` | table | defaults | See [`convention_files`](#convention_files). |
| `compaction` | table | defaults | See [`compaction`](#compaction). |

Path fields support `~` and `$VAR` expansion.

### `budgets`

Cost caps that gate a profile's turn loop.

| Key | Type | Default | Notes |
|---|---|---|---|
| `per_turn_usd` | float | `0.50` | Soft cap per turn. |
| `per_session_usd` | float | `5.00` | Hard cap per session. |
| `daily_usd` | float | `20.00` | Hard cap per UTC day. |

### `convention_files`

Controls loading of project convention files (`AGENTS.md`, `CLAUDE.md`,
`CAIRN.md`, etc.) into the system prompt.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Turn off to disable entirely. |
| `filenames` | list | `["CAIRN.md", "AGENTS.md", "CLAUDE.md"]` | Which filenames to look for, in order. |
| `walk_up_to` | `"git_root" \| "filesystem_root" \| "cwd_only"` | `"git_root"` | How far up to walk. |
| `search_subdirs` | bool | `true` | In monorepos, allow nested overrides. |
| `max_bytes_per_file` | int | `65_536` | Truncate at a paragraph boundary above this. |
| `trust_policy` | `"prompt" \| "always" \| "project_allowlist"` | `"prompt"` | First-encounter gating. |
| `user_level_paths` | list | `[]` | Absolute paths prepended to the search list. |

### `compaction`

Controls conversation-history truncation before each provider call.
Per-persona via profile (each persona has its own `compaction` block).
See [Compaction](compaction.md) for the full guide.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master kill switch; `false` disables truncation entirely. |
| `preserve_last_n_turns` | int | `6` | Hard floor on surviving turn blocks. Raise for tool-heavy personas. |
| `safety_margin_tokens` | int | `2048` | Held back from `context_window` on top of reserved output tokens. |
| `min_history_tokens` | int | `1024` | If effective budget falls below this, log ERROR and pass the request through unchanged. |

### `watcher`

Controls the file watcher introduced in 0.15.0. The watcher
snapshots a fixed set of paths on boot — every existing config
layer, every discovered convention file, and the three profile
docs (soul / user-context / MEMORY.md) — and polls them for
content changes.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Turn off to skip the boot-time watcher launch entirely. |
| `poll_interval_s` | float | `3.0` | Seconds between polls. Range 0.5–60. Each tick walks every watched path's `os.stat`; SHA-256 only fires when mtime drifted. |

### `tools`

Per-profile tool-runner knobs. Today only carries
`timeout_s_overrides` — a per-tool-name map that overrides the
`Tool.timeout_s` declared on each tool. The runner consults
this map on every dispatch; an absent key falls back to the
tool's declared default.

| Key | Type | Default | Notes |
|---|---|---|---|
| `timeout_s_overrides` | `dict[str, float]` | `{}` | Tool name → seconds. Each value bounded 1.0–3600.0. Unknown tool names log a warning at bootstrap and load cleanly (forward-compat across renames or removals). |

```toml
[profiles.default.tools.timeout_s_overrides]
web_fetch = 90.0
delegation = 300.0
```

There is no global `default_timeout_s` — the per-tool defaults
(read=10s, grep=30s, web_fetch=35s, delegation=120s) are
deliberately tuned and a single global value would either be
too short for delegation or too long for `file_read`. If you
keep hitting timeouts on one specific tool, override that
tool only.

### Wall-clock turn timeout

`OrchestratorConfig.max_turn_duration_s` (default **600.0** s)
is the wall-clock deadline for a single turn. Wired in 0.16.0:
a background watchdog task fires at the deadline and
soft-cancels the turn at the next iteration boundary inside
the orchestrator (between tool calls / between LLM
round-trips). Tools already in flight finish — the per-tool
`timeout_s` (above) bounds them independently, so the turn may
overshoot the wall-clock deadline by up to one
slow-tool-execution. The aborted turn surfaces as
`TurnAborted(reason="turn_timeout")` and the UI shows a
warning-tinted banner explaining that tool side-effects are
intact. Soft-cancel only — the watchdog never hard-cancels a
tool mid-call.

### Hot reload

When the watcher detects content changes (mtime + hash both
differ), Cairn surfaces a muted info banner. Drift is
*announce-only*: the running session continues with the
previously-loaded config until the user invokes `/reload`. The
watcher buffers events emitted during an active turn and emits
them on the next tick after the turn completes, so the banner
never appears above a streaming assistant message.

`/reload` performs a **surgical reload**, not a re-bootstrap:

- `SecretResolver`, `ModelRegistry`, and `ProviderRegistry` are
  rebuilt from the freshly-loaded `CairnConfig` and swapped onto
  the running orchestrator.
- The convention loader and profile-doc loader caches are
  invalidated so the next turn re-reads from disk.
- The file watcher's baseline is re-snapshotted, clearing the
  drift banner.
- The active session, persisted history, in-flight extraction
  worker, and Textual UI are all preserved.

`/reload` invoked mid-turn is queued and runs after the current
turn completes — the in-flight provider call finishes against
the bound registries, and the swap takes effect on the next turn.

If the new config fails validation (`load_config` raises
`ConfigError`), nothing is swapped: the user keeps the
previously-good config and the watcher's drift banner stays up
until they fix the file and reload again. See
[ADR 0042](decisions/0042-surgical-reload-boundary.md) for the
full rationale and the list of collaborators that are
deliberately *not* swapped on reload.

The watcher only watches paths that exist on boot — files
created afterwards (a freshly-added `.cairn/config.local.toml`,
a new ancestor `AGENTS.md`) are invisible until `/reload`
re-snapshots.

#### Role-pin changes don't rebind the active session

Each session is created bound to a concrete model id resolved at
session creation. If you edit a model's `roles` list — moving
`primary` from one model to another, say — the new role pin
applies to **future** sessions, not the active one. The active
session keeps using its original model.

`/reload` notices this case and surfaces a hint banner alongside
the success summary:

```
primary role now resolves to claude-haiku-4-5; the active session
stays on claude-opus-4-7. Restart cairn to switch.
```

This is intentional. Mid-session model swaps are risky in a way
that's specific to a session's *history* — provider-specific
tool-call id schemes, thinking-block contracts (Anthropic vs
DeepSeek vs OpenAI), and per-provider prompt-cache namespaces all
interact badly when the wire-format codec changes mid-conversation.
A future `/model <id>` command will be the explicit, opt-in path
for in-session model changes; until then, restart to pick up role
pins on an active conversation.

See [ADR 0042](decisions/0042-surgical-reload-boundary.md) for the
full reasoning.

### Delegation tools

Each delegation tool lets the companion consult a different model as if
it were a tool. Define via `[[profiles.<name>.delegation_tools]]` arrays.

| Key | Type | Default | Notes |
|---|---|---|---|
| `tool_name` | string | — (required) | Shown to the model. |
| `target_model` | string | — (required) | Model ID or `"role:<name>"`. |
| `description` | string | — (required) | Shown to the model. |
| `when_to_use` | string | — (required) | Guidance surfaced to the model. |
| `preserve_history` | bool | `false` | Pass full conversation vs. just the query. **Not yet implemented in V1 — setting this to `true` raises `NotImplementedError` at tool construction.** |
| `sub_system_prompt` | string | `null` | System prompt sent to the sub-session. Passed through as `ProviderRequest.system`; if `null`, no system prompt is sent. |
| `max_cost_usd` | float | `null` | Per-call cost cap in USD. On overflow the stream breaks early and the returned text is appended with `[delegation: cost cap reached; output truncated]`. |
| `approval_required` | bool | `false` | Ask the user before calling. |

## Secret references

API keys are never written to config files as plain strings. Every
`api_key` is a `SecretRef`: `<scheme>:<parameters>`.

| Scheme | Syntax | Behaviour |
|---|---|---|
| `keyring` | `keyring:<service>:<key>` | Resolves via the OS keychain via the `keyring` package. **Recommended.** |
| `env` | `env:<ENV_VAR>` | Reads from environment. |
| `prompt` | `prompt:<optional message>` | Prompts the user on first access, caches for the process lifetime. |
| `literal` | `literal:<value>` | Plaintext. **Rejected on any field marked as a secret** (including `api_key`). Kept in the parser so non-secret references can opt into inline values later. |

### Managing keyring entries

The recommended path is `cairn config secret set` —
see the [CLI reference](cli.md#secrets):

```bash
cairn config secret set keyring:cairn:anthropic-api-key
# Value for keyring:cairn:anthropic-api-key (input hidden):
```

`cairn config secret list` enumerates every secret reference in
the loaded config and probes its backing store read-only.
`cairn config secret delete <ref>` removes a keychain entry after
a y/N confirmation.

The lower-level `keyring` CLI (from the Python `keyring` package)
remains available for direct inspection:

```bash
keyring get cairn anthropic-api-key
```

### Path expansion

Path-valued keys (`soul_document_path`, `user_context_path`,
`memory_md_path`) expand `~` and `$VAR`:

```toml
soul_document_path = "~/.config/cairn/soul.md"
user_context_path = "$XDG_CONFIG_HOME/cairn/user_context.md"
```

## Full example

A complete, commented `config.toml` you can start from:

```toml
schema_version = 1
active_profile = "companion"

# ────────── Providers ──────────

[providers.anthropic]
api_key = "keyring:cairn:anthropic-api-key"

[providers.openai]
api_key = "keyring:cairn:openai-api-key"

# ────────── Models ──────────

[[models]]
id = "claude-opus-4-7"
provider = "anthropic"
display_name = "Claude Opus 4.7"
context_window = 200_000
max_output_tokens = 8_192
supports_tools = true
supports_vision = true
supports_prompt_cache = true
input_cost_per_1m = 15.00
output_cost_per_1m = 75.00
cache_read_cost_per_1m = 1.50
cache_write_cost_per_1m = 18.75
roles = ["primary", "reasoning"]

[[models]]
id = "claude-haiku-4-5-20251001"
provider = "anthropic"
display_name = "Claude Haiku 4.5"
context_window = 200_000
max_output_tokens = 8_192
supports_tools = true
input_cost_per_1m = 0.80
output_cost_per_1m = 4.00
roles = ["utility", "fast"]

# ────────── Profiles ──────────

[profiles.companion]
soul_document_path = "~/.config/cairn/soul.md"
user_context_path = "~/.config/cairn/user_context.md"
memory_md_path = "~/.config/cairn/MEMORY.md"
memory_space = "companion"
primary_model = "role:primary"
utility_model = "role:utility"

[profiles.companion.budgets]
per_turn_usd = 0.50
per_session_usd = 5.00
daily_usd = 20.00

[[profiles.companion.delegation_tools]]
tool_name = "ask_reasoning_model"
target_model = "role:reasoning"
description = "Delegate hard reasoning to the specialist."
when_to_use = "Multi-step deduction, tight logic, formal reasoning."
max_cost_usd = 0.50
```

## Related ADRs

- [0002 — Three-tier config merge](decisions/0002-three-tier-config-merge.md)
- [0003 — `SecretRef` schemes](decisions/0003-secret-ref-schemes.md)
- [0006 — Role-based model selection](decisions/0006-role-based-model-selection.md)
