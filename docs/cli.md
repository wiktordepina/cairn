# CLI

`cairn` is the console entry point. With no arguments it launches
the [Textual UI](ui.md) against the resolved profile; subcommands
manage configuration, secrets, and the project trust allowlist.

```
cairn [--profile NAME]                  # launch UI
cairn config show                       # print merged config (secrets redacted)
cairn config paths                      # list config files in resolution order
cairn config validate                   # exit 0 if valid, 1 otherwise
cairn config migrate [--write]          # apply schema migrations
cairn config init [--no-prompt]         # first-run setup
cairn config secret set <ref>           # store a secret in the OS keychain
cairn config secret list                # list secret references in the config
cairn config secret delete <ref>        # remove a keychain entry
cairn trust add [PATH]                  # trust a project for convention files
cairn trust list                        # list trusted projects
cairn trust remove [PATH]               # remove a trust entry
```

`--profile NAME` is honoured wherever it appears (root or before
any subcommand) and overrides the
[config resolution order](configuration.md).

## First-run setup

```
$ cairn config init
Cairn first-run setup.

Companion name [Cairn]: Atlas
Primary provider:
  1. anthropic
  2. openai
  3. openrouter
  4. deepseek
Choose [1]: 1
Store API key in the OS keychain now? [y/N]: y
API key for anthropic (input hidden): ********
✓ Stored in keyring (service=cairn, key=anthropic-api-key).
✓ Wrote /home/wiktor/.config/cairn/config.toml.
✓ Wrote stub /home/wiktor/.config/cairn/soul_document.md.
✓ Wrote stub /home/wiktor/.config/cairn/user_context.md.
✓ Wrote stub /home/wiktor/.config/cairn/MEMORY.md.

Run `cairn` to start a session.
```

`init` writes a minimal valid config plus three Markdown stubs
(`soul_document.md`, `user_context.md`, `MEMORY.md`) alongside it,
so first launch doesn't fall back to the bundled minimal identity.
Each stub is created only when the target doesn't already exist —
re-running `init` after a partial setup never trashes hand-edits.
The `config.toml` itself is the loud "already exists; refusing to
overwrite" case (exit 1).

`--no-prompt` skips elicitation and writes defaults verbatim;
useful for scripted setup. `--profile NAME` renames the default
profile (default `companion`).

If you run plain `cairn` with no configuration present, it prints a
redirect to `cairn config init` and exits 0 instead of trying to
launch.

## Inspecting configuration

`cairn config show` round-trips the merged config back to TOML,
with a comment header naming the layers and per-line annotations
on every `SecretRef`:

```
$ cairn config show
# merged from:
#   user     /home/wiktor/.config/cairn/config.toml
#   project  /home/wiktor/projects/cairn/.cairn/config.toml

schema_version = 1
active_profile = "companion"

[providers.anthropic]
api_key = "keyring:cairn:anthropic-api-key"  # → present in keyring
…
```

Annotations are read-only probes — `keyring.get_password` for
keyring refs, `os.environ.get` for env refs, no probing for
prompt / literal. `show` never resolves a `prompt:` reference and
never logs the plaintext of a stored secret.

`cairn config paths` reports every potential layer (`user`,
`project`, `local`) and whether each is currently loaded:

```
$ cairn config paths
user     /home/wiktor/.config/cairn/config.toml          (loaded)
project  /home/wiktor/projects/cairn/.cairn/config.toml  (loaded)
local    /home/wiktor/projects/cairn/.cairn/config.local.toml  (not present)
```

`cairn config validate` is silent on success (exit 0) and prints
the bubbled error message on failure (exit 1) — the documented
idiom is `cairn config validate && echo OK`.

## Secrets

The four secret schemes
([configuration.md → secret references](configuration.md)) cover
different storage strategies:

| Scheme | Where it lives | Managed by |
|---|---|---|
| `keyring:` | OS keychain (gnome-keyring, Keychain, Credential Manager) | `cairn config secret set / list / delete` |
| `env:` | Process environment | Your shell / process manager |
| `prompt:` | Memory only, prompted on first use | Resolved at use time |
| `literal:` | Verbatim in the TOML file | Edit the file directly |

`cairn config secret set <ref>` writes to the OS keychain. Only
`keyring:<service>:<key>` references are accepted — other schemes
are rejected with a remediation hint. If an entry already exists,
you'll be asked to confirm before overwriting (default `no`):

```
$ cairn config secret set keyring:cairn:anthropic-api-key
An entry already exists for service=cairn, key=anthropic-api-key. Overwrite? [y/N]: n
Aborted; keyring entry left unchanged.
```

`cairn config secret list` walks the loaded config and reports the
state of every secret reference:

```
$ cairn config secret list
keyring:cairn:anthropic-api-key   present in keyring   (providers.anthropic.api_key)
env:OPENAI_API_KEY                set                  (providers.openai.api_key)
prompt:OpenRouter API key         prompts on first use (providers.openrouter.api_key)
```

`cairn config secret delete <ref>` confirms before removing:

```
$ cairn config secret delete keyring:cairn:anthropic-api-key
Delete keyring entry service=cairn, key=anthropic-api-key? [y/N]: y
✓ Removed.
```

If the keychain backend is unavailable (e.g. `gnome-keyring` daemon
not running), `set` / `delete` exit with code **4** so scripts can
distinguish that recoverable state from a generic failure (exit 1).

## Trusting projects

The primary trust UX in everyday use is the in-app prompt
(`TextualPromptTrustGate`, shipped with the UI brick) — when you
open a project for the first time, cairn asks before loading any
convention files from it. `cairn trust …` is the **non-interactive
/ scripting alternative**, useful for:

- pre-trusting a project before cloning it,
- batch setup across many repos,
- inspecting or removing entries without launching the UI.

```
$ cd ~/projects/my-repo
$ cairn trust add
✓ Added /home/wiktor/projects/my-repo.

$ cairn trust list
/home/wiktor/projects/my-repo   (added 2026-04-25T18:42:00+00:00)

$ cairn trust remove
✓ Removed /home/wiktor/projects/my-repo.
```

`add` is idempotent — re-adding an exact path is a no-op with an
"already trusted" message. `remove` exits 1 if the path isn't in
the allowlist.

The allowlist lives at `$XDG_CONFIG_HOME/cairn/trusted_projects.toml`
and is shared across profiles.

## Schema migrations

`cairn config migrate` walks every loaded layer and reports the
schema version of each. With no migrations registered today, the
command always reports "current — no action" and exits 0:

```
$ cairn config migrate
user     /home/wiktor/.config/cairn/config.toml          schema_version=1 (current — no action)
nothing to migrate.
```

`--write` applies migrations in place. The pre-migration body is
backed up to `<path>.pre-migrate-<old_version>` first, so a bad
migration is recoverable by hand.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success / nothing to do |
| 1 | Generic failure (validation error, missing path, etc.) |
| 2 | Argument-parsing error (unknown flag, missing required argument) |
| 3 | Config exists, refusing to overwrite |
| 4 | Keyring backend unavailable |
