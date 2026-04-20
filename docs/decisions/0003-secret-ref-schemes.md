# 0003 — `SecretRef` with four schemes

**Date:** 2026-04-20
**Status:** accepted

## Context

LLM harnesses need API keys, and API keys are the single highest-value
thing a harness ever holds. Plaintext keys in config files are a
well-known anti-pattern — they leak via version control, shell history,
copy-paste into issues, backups, log files, and screen shares.

At the same time, cairn has to run in different environments: developer
workstations (where an OS keychain is natural), CI (where env vars
dominate), shared machines (where prompting is safer than storing),
and future automated environments (where something else again).

A common pattern is "store secrets in the OS keychain via `keyring`",
but committing to that exclusively rules out use cases where the
keychain isn't available (headless servers) or preferred.

## Decision

Every API-key field holds a `SecretRef`: a *reference* to where the
secret lives, not the value itself. Four schemes:

| Scheme | Syntax | Resolves to |
|---|---|---|
| `keyring` | `keyring:<service>:<key>` | `keyring.get_password(<service>, <key>)`. Recommended for workstations. |
| `env` | `env:<ENV_VAR>` | `os.environ[<ENV_VAR>]`. Fits CI. |
| `prompt` | `prompt:<optional message>` | Interactive prompt on first use; cached for the process. |
| `literal` | `literal:<value>` | Plaintext. **Rejected on any field marked as a secret** (e.g. `api_key`). |

`SecretRef` is a typed object in the Pydantic layer. The
`SecretResolver` service turns refs into values lazily — a ref is never
materialised unless it's about to be used.

`literal:` is explicitly rejected on secret fields at load time (raises
`ValidationError`). It's kept in the parser so that *non-secret*
references can opt into inline values in the future (e.g. for an
"override for tests" use case), without reintroducing the risk of
accidental plaintext keys.

## Consequences

**Easier:**

- The config file is safe to share. `api_key = "keyring:cairn:anthropic-api-key"`
  is not a leak.
- Moving between environments is a config change, not a code change.
- Lazy resolution means a key is never touched until the provider it
  unlocks is actually used.

**Harder:**

- First-run setup requires the user to put the key *somewhere*. The
  `keyring` CLI handles this on workstations; headless setups need a
  deliberate choice.
- Debugging "why is this ref failing to resolve?" requires knowing
  which scheme it uses. Error messages name the scheme to help.

**Ongoing cost:**

- A `SecretResolver` must be threaded through anything that needs a
  secret. The provider adapters hold a reference and call it on first
  use.
- Tests that exercise code paths needing a secret use a stub resolver
  returning a canned value.
