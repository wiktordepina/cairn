# 0044 — `cairn balance` fans out across configured providers

**Date:** 2026-04-26
**Status:** accepted

## Context

Two of the four V1 providers expose a public balance / credits API
reachable with the same key cairn already holds: DeepSeek's
`/user/balance` and OpenRouter's `/api/v1/credits`. The other two
do not — Anthropic's Admin Cost Report and OpenAI's
`/v1/organization/usage/*` both require a separate admin key.

The user-facing question: "how much credit do I have left?" The
shape of the answer depends on which providers the user has
configured. Cairn already has a CLI brick (0.14.0) with `cairn
config`, `cairn config secret`, `cairn trust` subcommands; a
`cairn balance` subcommand fits the same pattern.

Three design questions surfaced when scoping the brick:

1. Should the command take `--provider` to query one provider, or
   fan out across all configured ones?
2. For providers without a public balance API, should the row be
   hidden, or printed as a stub?
3. What's the exit-code convention for partial failure?

## Decision

**Fan out across every provider in `[providers.*]`, in parallel.**
No `--provider` flag in V1 — the user's implicit question is
"what's my overall position", not "what's my Anthropic position
specifically". A `--provider` flag is a future-friendly add if
needed.

**Print a stub row for endpoint-less providers**, not hide them.
The output looks like:

```
provider     remaining       used         source
anthropic    —               —            no public API
deepseek     CNY 110.00      CNY 0.00     /user/balance
openai       —               —            no public API
openrouter   USD 4.21        USD 0.79     /api/v1/credits
```

Stub-line was chosen over hiding because it answers the user's
implicit question — "why isn't my Anthropic balance shown?" —
without requiring them to consult docs. It's also forward-compat:
the day Anthropic ships a key-reachable endpoint (or the day cairn
adds an `admin_api_key` field), the stub becomes a real row with
no UX shape change.

**Exit codes:**

- `0` — every provider with an endpoint succeeded, OR all
  configured providers happen to be endpoint-less.
- `2` — every provider with an endpoint failed (network down,
  all keys invalid, etc.).

Partial failure (some succeed, some fail) returns `0` with the
failed provider's row carrying `error: <ExceptionType>` in the
`remaining` column. Picking `0` here makes the command safe to
run from cron / status scripts without alerting on transient
single-provider blips.

## Consequences

- Each `Provider` adapter implements `async balance() -> BalanceInfo
  | None`. `None` means "no public API"; raising means "tried and
  failed" — the CLI distinguishes them in the rendered row.
- The CLI uses `asyncio.gather` so a slow provider doesn't block
  others. The 10-second per-call timeout inside each adapter caps
  total wall-clock at ~10s even when every provider hangs.
- A future `--json` flag for machine consumption is anticipated but
  not in V1.
- A future `cairn balance --provider <name>` flag is anticipated
  but not in V1.

## Alternatives considered

- **`cairn balance <name>` (positional, single provider)**:
  forces the caller to know which providers are endpoint-capable.
  Rejected.
- **Hide endpoint-less providers**: leaves the user wondering
  whether their Anthropic config worked. Rejected.
- **Fail the whole command on any single error**: makes the
  command brittle for status-bar / cron use. Rejected.
- **Add an `admin_api_key` field to `ProviderConfig` so OpenAI /
  Anthropic admin endpoints become reachable**: real future work
  with its own ADR; out of scope for the brick that introduces
  the CLI surface.

## Implementation

- `cairn.cli._balance` — `register(app)` registers the command on
  the root Typer app (sibling to `config`, `trust`).
- `Provider.balance()` is part of the protocol; default
  implementation in each adapter returns `None` (Anthropic, OpenAI)
  or makes a small `httpx.AsyncClient` call (DeepSeek, OpenRouter).
- Tests in `tests/cli/test_balance.py` cover fan-out, partial
  failure, stub rendering, and the no-providers-configured edge.
