# Providers

Cairn speaks to LLM providers through a narrow `Provider` protocol:
streaming, tool calling, and token counting. Three adapters ship today:

| Provider | Adapter | Streaming | Tools | Native token counting |
|---|---|---|---|---|
| Anthropic | `AnthropicProvider` | Yes | Yes | Yes (native endpoint) |
| OpenAI (and OpenAI-compatible local servers) | `OpenAIProvider` | Yes | Yes | Via `tiktoken` with fallback |
| OpenRouter | `OpenRouterProvider` | Yes | Yes | Via `tiktoken` with fallback |

All adapters lazy-initialise their SDK client and lazy-resolve their
`api_key` on first use.

## Anthropic

Uses `anthropic.AsyncAnthropic.messages.stream()` under the hood.
Anthropic-native features (separate `system` parameter, inline
`tool_use` blocks, `image` source objects, thinking blocks) are
passed through without translation.

**Config:**

```toml
[providers.anthropic]
api_key = "keyring:cairn:anthropic-api-key"
```

**Token counting** uses the native `count_tokens` endpoint — the number
you get back is exactly what the model will see.

**Tool calling** uses Anthropic's native schema. No translation needed.

**Thinking blocks** are preserved when present; other providers strip
them when converting messages between formats.

## OpenAI

Uses `openai.AsyncOpenAI.chat.completions.create(stream=True)`.

**Config:**

```toml
[providers.openai]
api_key = "keyring:cairn:openai-api-key"
```

**Token counting** uses `tiktoken`. For models `tiktoken` doesn't know
about, the adapter falls back to a character-count estimate (roughly
4 chars per token).

**Tool calling** uses OpenAI's `tool_calls` array on assistant messages
and separate `role:tool` result messages. The adapter tracks the
`tool_call_index → id` mapping as the stream arrives.

### OpenAI-compatible local servers

Any OpenAI-compatible server (llama.cpp, vLLM, LM Studio, Ollama's
OpenAI endpoint, etc.) can be driven by the `OpenAIProvider` adapter
via `base_url`:

```toml
[providers.llamacpp]
base_url = "http://localhost:8080/v1"
# api_key is optional for most local servers; set it when your server requires one.
```

Configure each local server as a separate provider block, then refer to
it from a `[[models]]` entry:

```toml
[[models]]
id = "llama-3.3-70b-instruct"
provider = "llamacpp"
# ... rest of the model metadata
```

## OpenRouter

OpenRouter serves many underlying models through an OpenAI-compatible
API. We ship a separate adapter because OR has provider-specific
identity headers, billing semantics, and (soon) fallback-chain config.

**Config:**

```toml
[providers.openrouter]
api_key = "keyring:cairn:openrouter-api-key"
extra_headers = { "HTTP-Referer" = "https://github.com/wiktordepina/cairn", "X-Title" = "Cairn" }
```

`HTTP-Referer` and `X-Title` are recommended for OpenRouter's per-site
attribution and metric breakdowns.

**Token counting** uses `tiktoken` with the same fallback as the OpenAI
adapter. OpenRouter serves models that `tiktoken` won't have tokenisers
for; the estimate is coarse but monotonic.

### Deferred features

- **Fallback chains** (`route` parameter) — OpenRouter can fail over to
  alternate models automatically. Planned for V2.
- **Per-provider cost reporting** — OpenRouter returns provider-specific
  cost info in response headers. Not yet surfaced.

## DeepSeek

[DeepSeek](https://platform.deepseek.com) ships an OpenAI-compatible
API at `https://api.deepseek.com/v1`. Two relevant V1 models:

- `deepseek-chat` — general-purpose model, tool-use capable.
- `deepseek-reasoner` (and the V4-class thinking SKUs) — stream
  reasoning traces alongside the final answer. Reasoning content
  is captured into a `ThinkingBlock` on the assistant message and
  re-emitted on the next turn as `reasoning_content` —
  thinking-mode SKUs reject multi-turn requests that drop the
  trace. The trace is round-tripped invisibly; surfacing it in the
  transcript is a follow-up.

**Config:**

```toml
[providers.deepseek]
api_key = "env:DEEPSEEK_API_KEY"
```

**Caching** is automatic and disk-backed — no markers, no opt-in.
DeepSeek surfaces hits via `usage.prompt_cache_hit_tokens` (a third
distinct shape compared to Anthropic and OpenAI); cairn maps it onto
`UsageEvent.cache_read_tokens`. Cache hits are billed at roughly 10 %
of the input rate, so caching pays back on the very first re-use.

**Token counting** falls back to character-based estimation —
DeepSeek does not publish a tiktoken-compatible tokenizer.

## Adding a model

Ship cairn with a `[[models]]` entry for every model you want to use.
Adding a model is a config change — no code change required:

```toml
[[models]]
id = "gpt-4o-mini-2024-07-18"
provider = "openai"
display_name = "GPT-4o mini"
context_window = 128_000
max_output_tokens = 16_384
supports_tools = true
supports_vision = true
input_cost_per_1m = 0.15
output_cost_per_1m = 0.60
roles = ["utility", "fast"]
```

Refer to it from a profile by vendor ID (`gpt-4o-mini-2024-07-18`) or
by role (`role:utility`).

## Adding a new provider

Not a config change — a code change. Every new provider needs:

1. A new `@runtime_checkable` implementer of the `Provider` protocol.
2. Its adapter registered in `ProviderRegistry.register_adapter(...)`.
3. Translation code that maps cairn's `Message` / `ContentBlock` types
   to the vendor's wire format (and back, for tool calls).
4. Error mapping to cairn's `ProviderError` hierarchy.
5. Tests against real responses where possible; against recorded
   fixtures otherwise.

See the existing adapters in `src/cairn/providers/` for the shape. The
Google `google-genai` adapter is deferred to V2 because its API shape
differs enough to warrant its own translation code.

## Error hierarchy

All provider errors derive from `ProviderError(provider, retryable)`.
Specific subtypes:

| Error | Retryable | Notes |
|---|---|---|
| `AuthenticationError` | No | Bad API key. Fix config. |
| `ModelNotAvailableError` | No | Unknown model, or gated model you don't have access to. |
| `RateLimitError(retry_after)` | Yes | SDK-level retries are already configured; this is what surfaces after retries exhaust. |
| `ProviderOverloadedError` | Yes | Typically a 5xx from the vendor. |

The SDKs (`anthropic`, `openai`) handle transport retries internally;
they surface the error only after their own retry budget is exhausted.
Cairn does not layer additional retries on top of the transport.

## Cross-provider request fields

`ProviderRequest` carries a small set of fields that translate
differently per adapter. The orchestrator sets them; each adapter
maps them to its native shape (or silently ignores when there's no
equivalent).

- **`reasoning_effort`** — unified knob for thinking-capable models.
  Accepts `"none" | "minimal" | "low" | "medium" | "high" | "xhigh"`.
  See [ADR 0043](decisions/0043-unified-reasoning-effort.md). Maps:
  - **OpenAI** o-series / gpt-5-class: passed through verbatim.
  - **Anthropic** with `supports_thinking`: scaled proportionally
    to `max_tokens` (low/medium/high/xhigh = 25/50/75/90 %),
    floored at 1024, ceilinged at `max_tokens - 1`.
  - **DeepSeek**: forwarded on V4-pro-class; no-op on
    `deepseek-reasoner` (always thinks).
  - **OpenRouter**: best-effort passthrough.

- **`tool_choice`** — `"auto" | "any" | "none"` or
  `("tool", "<tool_name>")`. Anthropic's `"any"` semantically equals
  OpenAI's `"required"` — adapters translate. With Anthropic
  extended thinking on, only `"auto"` and `"none"` are supported;
  `"any"` and `"tool"` are downgraded to `"auto"` with a WARNING.

- **`disable_parallel_tool_use`** — when True, instruct the provider
  to call tools sequentially. Honoured by Anthropic (via
  `tool_choice.disable_parallel_tool_use`) and OpenAI (via
  `parallel_tool_calls=false`).

- **`prompt_cache_key`** — stable cache-prefix partition key.
  Forwarded to OpenAI's `prompt_cache_key` field; ignored elsewhere
  (other adapters' caches don't expose a partition knob).

`UsageEvent` carries two breakdown fields populated by adapters
when the upstream surfaces them:

- **`reasoning_tokens`** — share of `output_tokens` spent on
  reasoning. Populated by OpenAI's
  `completion_tokens_details.reasoning_tokens` on o-series models.
- **`cache_discount_usd`** — USD discount already applied for cache
  hits. Populated by OpenRouter's `cache_discount` field uniformly
  across upstreams.

## Account balance

Adapters expose an optional `balance() -> BalanceInfo | None`
method. DeepSeek and OpenRouter return live numbers from
`/user/balance` and `/api/v1/credits` respectively; Anthropic and
OpenAI return `None` because their billing endpoints require a
separate admin key. The `cairn balance` CLI surfaces the result —
see [`cli.md`](cli.md#account-balance) and
[ADR 0044](decisions/0044-balance-cli-fanout.md).

## Related ADRs

- [0005 — Per-provider adapters over unified client](decisions/0005-per-provider-adapters.md)
- [0006 — Role-based model selection](decisions/0006-role-based-model-selection.md)
- [0043 — Unified `reasoning_effort` knob across providers](decisions/0043-unified-reasoning-effort.md)
- [0044 — `cairn balance` fans out across configured providers](decisions/0044-balance-cli-fanout.md)
