## Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is pre-1.0, minor version bumps track meaningful additions
of functionality; patch bumps cover docs, internal tidy-ups, and fixes that
don't change the public surface. Everything is still in flux.

## [Unreleased]

UI, CLI entry point, and observability tranche 2 (log redaction,
structured `UIEventObserver`) are still to land.

## [0.9.0] — 2026-04-23

Ships project convention-file loading: `AGENTS.md` / `CLAUDE.md` /
`CAIRN.md` discovery, a pluggable trust gate, and injection into
the system prompt as a dedicated `<project_conventions>` segment.
Design doc: `.plan/conventions-brick-design.md`.

### Added — conventions

- **`cairn.conventions` module** (`src/cairn/conventions/`):
    - `discover_project_files` + `discover_user_files` — walk up
      from cwd to a configurable boundary (git root by default),
      take the nearest match per filename, with optional nested
      descent for monorepos. Skips common build/dep dirs, hidden
      dirs, and symlinks. Capped by `max_nested_depth` (default 3).
      User-level fallback paths open verbatim with `~`/`$VAR`
      expansion.
    - `TrustGate` protocol with three V1 implementations:
      `AlwaysTrustGate`, `AllowlistTrustGate` (backed by
      `AllowlistStore` reading
      `$XDG_CONFIG_HOME/cairn/trusted_projects.toml`), and
      `DenyingPromptTrustGate` — a placeholder for
      `trust_policy="prompt"` until the UI brick lands, DENYs
      with a once-per-project WARNING. See
      [ADR 0031](docs/decisions/0031-trust-prompt-deferred.md).
    - `AllowlistStore` with full add / remove / list / contains
      surface — V1 uses only `contains()`, but the other methods
      ship now so the CLI brick's `cairn trust` subcommand has a
      stable target.
    - `ConventionLoader` — caches discovery + trust check + read
      per session. `enabled=False` short-circuits with no disk
      I/O. `invalidate()` drops the cache for `/reload`.
    - `ConventionFile` frozen dataclass + `wrap_one` /
      `render_conventions` helpers emit the
      `<project_conventions source="..." path="...">` envelope
      per arch doc §4.12.
- **`ConventionFilesConfig.max_nested_depth`** new field
  (default 3) — caps nested descent. Additive pydantic default,
  no TOML migration.
- **`StandardContextManager` extended** with optional
  `conventions: ConventionLoader | None` kwarg. When provided,
  a `<project_conventions>` section lands between
  `<user_context>` and `<memory_index>` — matches arch doc §4.12
  assembly order. `conventions=None` preserves the pre-brick
  layout (regression guard).
- **Read handling**: UTF-8 with `errors="replace"`, 64 KiB
  default cap per file, paragraph-boundary truncation with an
  HTML-comment suffix recording original size. Binary files
  (null byte in first 1 KiB) skipped with a WARNING.

### Added — docs + ADRs

- New guide page `docs/conventions.md` walking through
  discovery, the trust gate, user-level fallbacks, size caps,
  and the system-prompt envelope format.
- [ADR 0030 — Convention-file ordering](docs/decisions/0030-convention-file-ordering.md)
  captures the broad-to-specific layering decision.
- [ADR 0031 — `trust_policy="prompt"` deferred](docs/decisions/0031-trust-prompt-deferred.md)
  explains the conservative default pending UI.
- [ADR 0032 — No Cursor / Cline in V1](docs/decisions/0032-convention-files-no-cursor-cline.md)
  keeps the default filename list tight.
- `docs/architecture.md` status table: convention files marked
  Shipped at 0.9.0.
- Auto-generated `docs/reference/conventions.md`.

### Added — tests

- **+52 tests** across `tests/conventions/` (22 discovery, 15
  trust, 9 loader, 3 render) and `tests/memory/test_context.py`
  (+3 integration tests covering the new section's position and
  None-fallback behaviour).

## [0.8.0] — 2026-04-22

Ships basic compaction: turn-block truncation in front of every
provider call, with a preserve-floor that escalates to a user
advisory rather than silently blocking or silently overflowing.
Design doc: `.plan/compaction-brick-design.md`.

### Added — compaction

- **`cairn.compaction` module** (`src/cairn/compaction/`):
    - `TruncatingCompactor` — implements the
      `MessagePreparer` protocol. Computes the effective budget
      (`context_window − max_tokens − safety_margin_tokens`), counts
      tokens via the provider's native tokenizer, and drops whole
      turn blocks from the front of `ProviderRequest.messages`
      until the request fits or the preserve-floor is reached.
    - `iter_turn_blocks` + `TurnBlock` — detect coherent turn
      boundaries so truncation never splits a
      `ToolUseBlock`/`ToolResultBlock` pair or leaves the first
      message with a non-user role. See
      [ADR 0028](docs/decisions/0028-turn-block-granularity.md).
    - `BudgetOverflowGateway` protocol with two default stubs:
      `AutoContinueOverflowGateway` and
      `AutoTerminateOverflowGateway`. The terminate stub is the
      orchestrator default so headless/CI runs fail loudly.
    - `BudgetOverflowDeclined` exception signalling the terminate
      path back to the orchestrator.
- **`CompactionConfig`** on `ProfileConfig.compaction` — four
  knobs (`enabled`, `preserve_last_n_turns`,
  `safety_margin_tokens`, `min_history_tokens`). Per-persona by
  construction because each persona is its own profile.
- **Two new UI events** in `cairn.domain`: `HistoryCompacted`
  (normal compaction + preserve-floor-continue paths) and
  `BudgetOverflowAdvisory` (preserve-floor hit; decision
  pending).
- **Orchestrator wiring** — catches `BudgetOverflowDeclined`, marks
  the turn aborted with `reason="user_declined_overflow"`,
  archives the session via `SessionManager.archive`, and emits
  `TurnAborted` + `SessionArchived`. See
  [ADR 0029](docs/decisions/0029-context-budget-advisory.md).

### Added — docs + ADRs

- New guide page `docs/compaction.md` covering what compaction
  does, what it doesn't, the advisory-budget flow, config knobs,
  and UI events.
- `docs/configuration.md` — new `[profiles.<name>.compaction]`
  section.
- Three ADRs: [0027](docs/decisions/0027-truncation-before-summarisation.md),
  [0028](docs/decisions/0028-turn-block-granularity.md),
  [0029](docs/decisions/0029-context-budget-advisory.md).
- Auto-generated reference: `cairn.compaction` added to
  `REFERENCE_MODULES`; `docs/reference/compaction.md` generated.
- `docs/architecture.md` status table updated — compaction shipped
  at 0.8.0.

### Tests

24 new compaction tests (8 turn-block + 13 preparer + 3 gateway
stubs) plus 2 new orchestrator integration tests covering the
continue and terminate paths end-to-end.

## [0.7.0] — 2026-04-22

Ships tier-1 memory end-to-end: post-turn observation extraction with
dedup-on-store, composite-scored retrieval, and a context manager that
assembles soul / user-context / MEMORY.md alongside retrieved
memories. Also wraps up the observability bootstrap modules and the
auto-generated API reference that had been accumulating on `main`.

### Added — tier-1 memory

- **Migration `0003_memory.sql`** — `memory_entries` table with CHECK
  constraints on `importance` (1–10), `memory_class`
  (`semantic`/`episodic`) and `entry_type` (six values). Plus an
  external-content FTS5 shadow (`memory_entries_fts`) and the standard
  `ai`/`ad`/`au` sync triggers. No ON DELETE CASCADE from sessions —
  memory outlives its source session ([arch doc §4.11 invariant #4]).
- **`MemoryRepo`** (`src/cairn/persistence/_memory_repo.py`) — `store`
  with dedup-on-store (`SequenceMatcher ≥ 0.90` against FTS5
  candidates, `BEGIN IMMEDIATE`-wrapped; refresh `updated_at` + max
  importance rather than inserting a second row), `search` (BM25
  with optional `entry_types` filter), plus `recent`, `get`, `delete`,
  `count_for_space`.
- **`MemoryHit`** record pairs each search result with its BM25 score
  for the retrieval service.
- **`cairn.persistence._text.significant_words`** — hand-rolled
  40-word English stopword filter + tokeniser powering the dedup
  candidate query. No external NLP dep.
- **`ObservationLog`** (`src/cairn/memory/_observation_log.py`) —
  append-only JSONL writer. One file per UTC day under
  `<profile>/memory/observations/YYYY-MM-DD.jsonl`. Dir created
  `0o700` lazily, `asyncio.Lock` keeps concurrent writers from
  interleaving bytes. `Observation.from_entry(entry)` converts a
  persisted `MemoryEntry`. No fsync per append by default; SQLite is
  the primary durability store.
- **`Extractor`** (`src/cairn/memory/_extractor.py`) — streams
  `ModelRole.EXTRACTION` (falls back to `UTILITY` when unset), parses
  the structured JSON envelope, stores via `MemoryRepo`, mirrors to
  `ObservationLog`, records usage with
  `operation=UsageOperation.EXTRACTION` and `role="extraction"`.
  Tolerates ```json fenced responses. Mid-stream cost cap
  (`MemoryConfig.max_extraction_cost_usd`) truncates runaway calls;
  parse failures drop the batch but still record usage. Exceptions
  are logged and swallowed — the worker keeps running.
- **`ObservationExtractionQueue`** (`src/cairn/memory/_queue.py`) —
  implements the orchestrator's `ExtractionQueue` protocol:
  non-blocking `submit`, background `asyncio.Task` worker, per-space
  `asyncio.Lock` for serialisation (cross-space jobs run in
  parallel), drop-oldest-on-overflow with a logged WARNING at
  `max_pending_extractions`. Gates: length gate
  (`min_extraction_chars`), persona opt-out (`extract_from_personas`),
  tool-only-turn gate (`extract_from_tool_only_turns`). Graceful
  `stop()` waits for in-flight extractions to drain.
- **`MemoryService`** (`src/cairn/memory/_retrieval.py`) — implements
  the orchestrator's `MemoryService` protocol. `retrieve(space,
  query, k)` fetches `3*k` BM25 candidates, rescores with the
  arch-doc composite (`0.3·recency + 0.3·importance + 0.4·relevance`
  with 90-day semantic / 3-day episodic half-lives), sorts desc,
  truncates to top-k, truncates content to
  `retrieval_content_truncate`. `composite_score()` exported as a
  pure function; golden-value tests pin the formula.
- **`ProfileDocLoader` + `StandardContextManager`**
  (`src/cairn/memory/_context.py`) — default `ContextManager`
  implementation. Reads `soul_document.md`, `user_context.md`, and
  `MEMORY.md` (verbatim, as an index file per
  [ADR 0024](docs/decisions/0024-memory-md-as-index.md)) with a 20 KB
  per-file defensive cap + WARNING. Missing soul doc falls back to a
  bundled minimal identity. System prompt assembled as tagged
  sections — `<identity>`, `<user_context>`, `<memory_index>`,
  `<retrieved_memories>`, `<persona_system_prompt>` — with empty
  sources omitted entirely. Retrieved memories render as
  `- [<type>] <content> (importance <n>)` bullets.
- **`ModelRole.EXTRACTION`** — new enum member for dedicated
  extraction models ([ADR 0025](docs/decisions/0025-extraction-role-and-local-models.md)).
- **`MemoryConfig`** on `ProfileConfig.memory` — nine tunables:
  `extraction_context_turns`, `min_extraction_chars`,
  `extract_from_personas`, `extract_from_tool_only_turns`,
  `max_extraction_cost_usd`, `max_pending_extractions`,
  `retrieval_k`, `retrieval_content_truncate`,
  `observation_log_fsync`.
- **`memory_dir_for_profile()`** path helper in `cairn.persistence`.
- **`cairn.memory` package** (`__init__.py` with `__all__`) re-exports
  the public surface — `Extractor`, `ObservationExtractionQueue`,
  `MemoryService`, `StandardContextManager`, `ProfileDocLoader`,
  `ObservationLog`, `composite_score`, plus Pydantic response
  models.
- **New guide page at `docs/memory.md`** — end-to-end walkthrough of
  extraction, retrieval, system prompt assembly, on-disk layout,
  invariants, configuration, and troubleshooting.
- **Six new ADRs** — [0021](docs/decisions/0021-tier-1-memory-first.md),
  [0022](docs/decisions/0022-memory-dedup-threshold.md),
  [0023](docs/decisions/0023-jsonl-log-no-rebuild.md),
  [0024](docs/decisions/0024-memory-md-as-index.md),
  [0025](docs/decisions/0025-extraction-role-and-local-models.md),
  [0026](docs/decisions/0026-per-turn-extraction-with-context.md).

### Added — observability bootstrap

- **`cairn.logging.setup_logging()`** — idempotent bootstrap for the
  `cairn` namespace logger. `RotatingFileHandler` (5 MB × 3 backups),
  `propagate = False` (Textual UI owns the terminal), env-driven via
  `CAIRN_LOG_LEVEL` and `CAIRN_LOG_FILE`, default path from
  `platformdirs.user_log_path("cairn")`. Parent directory created
  with mode `0o700`.
- **`cairn.ssl.setup_ssl()`** — idempotent bootstrap injecting the
  operating system's trust store into Python's `ssl` module via
  `truststore`. Silent no-op if `truststore` is unavailable or
  injection raises. Unblocks provider HTTPS calls behind corporate
  MITM proxies (Netskope, Zscaler, etc.).
- **New guide page** at `docs/observability.md` — resolution order,
  idempotency semantics, and when to call each bootstrap.

### Added — dependencies

- **`truststore>=0.9`** as a direct runtime dependency.

### Added — auto-generated API reference

- **`docs/gen_ref_pages.py`** — standalone generator. Reads each
  module's `__all__`, recovers the `# Section` comment groupings,
  and writes `docs/reference/<module>.md` with one `:::` directive
  per public symbol. Supports `--check` for drift detection in CI.
  See [ADR 0020](docs/decisions/0020-auto-generated-api-reference.md).
- **Coverage extended** to every package and single-file module —
  `config`, `domain`, `logging`, `orchestrator`, `persistence`,
  `providers`, `ssl`, `tools`. The previous hand-curated set was
  missing `config` and `persistence` entirely.
- **Drift test** at `tests/docs/test_gen_ref_pages.py` — runs the
  generator with `--check` against the real repo state. Fails locally
  before push if `__all__` was edited without regenerating.
- **`__all__` added to `cairn.logging` and `cairn.ssl`** so the
  generator has uniform input across packages and single-file
  modules.

### Changed — documentation

- **Google-style docstrings adopted as the house convention**
  ([ADR 0019](docs/decisions/0019-docstrings-google-style.md)). Inline
  code uses single backticks; structured `Args:` / `Returns:` /
  `Raises:` sections are used where they clarify; plain-prose
  docstrings remain acceptable for simple cases. Convention documented
  in `AGENTS.md`.
- **Existing docstrings tidied.** RST-style double-backticks
  (`` ``foo`` ``) rendered literally in `mkdocstrings` output;
  converted to single-backticks (`` `foo` ``) across 57 files
  (`src/cairn/` and `tests/`). Docstring-only changes, no behaviour
  change.

## [0.6.0] — 2026-04-21

Wires the real tool system into the orchestrator, completing the
tool-system brick for V1. The runner drives per-call lifecycle and
audit; `DelegationTool` lets the companion consult specialist models
in ephemeral sub-sessions; the orchestrator now calls the runner on
both approve and reject paths so the `approval_decisions` audit row
always lands.

### Added — `DefaultToolRunner` (`src/cairn/tools/_runner.py`)

- **Per-call lifecycle driver** — replaces the orchestrator's
  `RaisingToolRunner` stub. Drives every transition on the
  `tool_calls` row: `pending → (approved | rejected) → executing →
  completed | failed | timed_out`.
- **Approval audit** — writes one row per dispatched call to
  `approval_decisions`, on both approve and reject paths.
- **Timeout enforcement** — wraps `tool.invoke` in
  `asyncio.timeout(tool.timeout_s)`; on expiry, transitions the row
  to `TIMED_OUT` and returns an error result.
- **Error classification** — `PathEscape`, `SSRFBlocked`,
  `ToolTimeout`, and `ToolError` map to `ErrorClass.USER`; anything
  else maps to `ErrorClass.UNEXPECTED`.
- **`tool_use_id` stamping** — fills in the blank `tool_use_id`
  returned by tools (the convention the `@tool` decorator and
  `DelegationTool` both follow) so the provider can correlate the
  result to the tool call.
- **`decided_by` narrowing** — richer approver strings (`"auto:read-only"`,
  `"user:session-allowlist"`) collapse to the DB enum (`"auto"` or
  `"user"`) by prefix.

### Added — `DelegationTool` (`src/cairn/tools/_delegation.py`)

- **Sub-session spawning** — creates an ephemeral child session with
  `parent_session_id = ctx.session.id` for cost attribution and
  lineage.
- **Mid-stream cost cap** — when the accumulated cost for the
  delegation exceeds `DelegationToolConfig.max_cost_usd`, streaming
  breaks early and the returned text is appended with
  `[delegation: cost cap reached; output truncated]`.
- **Usage tracking** — records the final `UsageEvent` via
  `CostTracker.record(..., operation=UsageOperation.DELEGATION,
  parent_session_id=…)`.
- **Sub-session archival** — runs in a `finally:` block, so the
  ephemeral session is archived even if the provider stream raises.
- **History guard** — `DelegationToolConfig.preserve_history = true`
  raises `NotImplementedError` at tool construction. The config
  field shape is preserved for a future wiring.
- **Input schema** — `DelegationArgs(prompt: str)`.

### Changed — `ToolRunner` protocol and orchestrator wiring

- **`ToolRunner.run` widened** with `decision: ApprovalDecision` and
  `message_id: str` kwargs. The orchestrator passes both through;
  the runner uses them to stamp `message_id` on the `tool_calls`
  row and to record the approval outcome.
- **`Orchestrator._dispatch_tools`** now calls the runner **once per
  tool call, on both approve and reject paths**. On REJECT, the
  runner writes the rejection audit row and synthesises the error
  block; the orchestrator still emits the `ToolCallRejected` UI
  event. The orchestrator continues to own the approval chain, the
  `ResultTransformer` chain, and per-call timing.
- **`RecordingToolRunner` test fake** (in `tests/orchestrator/_fakes.py`)
  updated to mirror the new contract — on REJECT it returns an
  error block without invoking the handler.

### Fixed

- `DefaultToolRunner` now stamps `tool_call.id` onto the returned
  `ToolResultBlock` when the tool left `tool_use_id` blank. Without
  this, successful results would have flowed to the provider with
  an empty id, breaking tool-call correlation.

### Dependencies

- Declared `httpx` as a direct runtime dependency. `httpx` was
  already transitively installed via the provider SDKs and imported
  directly by `web_fetch` + the SSRF defence module; the declaration
  makes the dependency explicit.

### Deferred

- **`DelegationSpawned` / `DelegationCompleted` UI events.** The
  domain events exist and `DelegationTool` runs, but the
  orchestrator cannot emit them yet — the sub-session ID is created
  inside `DelegationTool.invoke()` and the `Tool` protocol has no
  back-channel to report it. Resolving needs either a `TurnContext`
  callback or a post-hoc `SessionRepo.children_of()` query; picked
  up in a follow-up release. Users can still observe delegation via
  `model_usage` rows where `operation = delegation` and via
  `SessionRepo.children_of(parent)`.
- **`preserve_history = true`** for delegation — raises at
  construction in V1.

### Tests

Total suite: **566 passed** (537 on 0.5.0 + 29 new: 18 for the
runner, 11 for the delegation tool; orchestrator integration tests
green against the rewired dispatcher). Ruff + pyright clean.

## [0.5.0] — 2026-04-21

The tool-system foundation plus V1's four built-in tools. The
orchestrator's `RaisingToolRunner` stub is still in place — the
runner + orchestrator wiring lands in a subsequent release. What
ships here is everything the tool system exposes: the decorator,
the registry, the approval and transformation middleware, the
security primitives, and the built-in tool catalogue.

### Added — Tool-system foundation (`src/cairn/tools/`)

- **`@tool` decorator** — wraps an async callable with a Pydantic
  args model into a `Tool` protocol-conforming instance. Validates
  risk-tier / side-effects consistency at decoration time; generates
  JSON `input_schema` from `args_model.model_json_schema()`; defaults
  `approval_required` from the tier.
- **`DefaultToolRegistry`** — session-type-scoped tool discovery.
  Ephemeral sessions see nothing by default (override via
  `ephemeral_allowlist`); persona sessions see a per-persona
  allowlist; companion sessions see the full set
  (`companion_tools + mcp_tools`). Duplicate names across the sets
  are a config error.
- **`ApprovalDecisionRepo`** — CRUD over the
  `approval_decisions` table that migration 0002 provisioned. Writes
  one row per terminal approval outcome (approved / rejected) with
  the redacted args snapshot.
- **Error hierarchy** — `ToolError`, `ToolRetry`, `ToolTimeout`,
  `PathEscape`, `SSRFBlocked`. Classified for distinct runner
  responses (expected-failure result block, one retry, timeout
  marker, sandbox escape, SSRF block).
- **`Tool` protocol extensions** — `description`, `input_schema`,
  `tool_kind` on the protocol so the registry can build
  `ToolDefinition`s and the persistence layer can tell a delegation
  call apart from a native one.

### Added — Security middleware

Three `ResultTransformer`s and three `ToolApprover`s, ready to wire
into the orchestrator's existing middleware chains.

Transformers, applied in order `InvisibleUnicodeStripper →
SecretRedactor → SpotlightTransformer`:

- **`InvisibleUnicodeStripper`** — removes zero-width, bidi-override,
  bidi-isolate, and Unicode-Tag-plane codepoints. Applied to tool
  outputs only. Runs first so an attacker cannot split an API-key
  across regex boundaries with an invisible separator.
- **`SecretRedactor`** — pattern-based replacement for
  `Bearer …`, `sk-…`, `sk-ant-…`, `AIza…`, `AKIA…`. Patterns
  from security doc §4.
- **`SpotlightTransformer`** — wraps the result in a
  `<tool_result tool="…" trust="untrusted">…</tool_result>`
  envelope with a plain-English warning. Works on both string and
  `list[ContentBlock]` content.

The strip-first order is pinned by a counter-example test that
fails if the chain is reordered.

Approvers, default chain `[AutoApproveReadOnly, SessionAllowlist,
TierGate]`:

- **`AutoApproveReadOnly`** — approves read-only (`side_effects ∈
  {"none", "read"}`) tier ≤ 2 calls.
- **`SessionAllowlist`** — caches `(tool_name, args_signature)`
  tuples where `args_signature = sha256(sorted_json(args))`.
  First-run prompts; exact repeats auto-approve. Populated by the
  harness-assembly layer on user approval.
- **`TierGate`** — escalates every call unconditionally as a
  belt-and-braces check; guarantees tier-4+ reaches the gateway
  even if earlier approvers misfire.

### Added — Security primitives (`src/cairn/tools/security/`)

- **`WorkspaceSandbox`** — rejects absolute paths, rejects `..`
  escapes, rejects symlink escapes via `Path.resolve()` +
  `relative_to()`. `assert_readable` / `assert_writable` for
  belt-and-braces checks on paths constructed by other means.
- **SSRF defence module** — `validate_url`, `resolve_hostname`,
  `safe_fetch`. Scheme allowlist (`http`, `https`), blocked-network
  list covering RFC1918, loopback, link-local (incl. cloud metadata
  `169.254.169.254`), IPv6 ULA / link-local / unspecified, and
  carrier-grade NAT. Any single resolved IP in a blocked range
  fails the call. Embedded-credential URLs rejected. `safe_fetch`
  disables automatic redirects and caps response size via streaming
  reads.

IP pinning against DNS rebinding is deferred — see ADR 0017.

### Added — Built-in tools (`src/cairn/tools/builtin/`)

Each tool is built via a `make_<tool>(…)` factory so the CLI can
bind it to whichever workspace root is in scope.

- **`file_read`** (Tier 1) — sandboxed UTF-8 read with a 1 MB cap
  (`... [truncated: N bytes total]` suffix on overflow) and binary
  detection via null-byte probe returning
  `[binary: <mime>, <size> bytes, sha256:<prefix>]`.
- **`file_write`** (Tier 3, approval-required) — `create` /
  `overwrite` / `append` modes, 1 MB write cap, parent directory
  must already exist, refuses to clobber non-regular targets.
- **`grep`** (Tier 1) — regex search under the sandbox, 200-match
  cap with truncation notice, skips binaries / hidden dirs /
  oversize files. Uses ripgrep (`--no-follow`,
  `--max-filesize=N`, `--max-count=N`, `-e pattern`, argv-form,
  no shell) when `rg` is on `PATH`; pure-Python `re` +
  `pathlib.rglob` fallback otherwise.
- **`web_fetch`** (Tier 2) — full SSRF defence re-run at every hop,
  including each redirect target; manual redirect walk with a hard
  hop cap; Content-Type filter (text/*, application/json,
  application/xml, application/javascript); HTML best-effort
  stripped to plain text (script and style bodies removed, entities
  unescaped, whitespace collapsed); binary responses return the
  same summary form as `file_read`.

### Added — User documentation

- `docs/tools.md` — the tool system: Tool protocol, risk tiers,
  session-type scoping, the `@tool` decorator with a worked
  example, workspace sandbox, SSRF defence, security middleware
  details, built-in catalogue, custom-tool registration, what's
  not yet shipped.
- `docs/decisions/0013-tier-taxonomy.md` — why 0-4 tiers, why not
  more or fewer, and the approval policy per tier.
- `docs/decisions/0014-tool-decorator.md` — why the `@tool`
  decorator over a class hierarchy.
- `docs/decisions/0015-session-allowlist-exact-match.md` — why
  the allowlist matches on `(tool_name, args_signature)` rather
  than tool-level or path-scoped approval.
- `docs/decisions/0016-no-shell-in-v1.md` — why V1 does not ship
  `bash` / `code_exec` and what would need to land before it does.
- `docs/decisions/0017-ssrf-ip-pinning-deferred.md` — why IP
  pinning against DNS rebinding is out of V1, and the upgrade
  path.
- `docs/architecture.md` — tool system status bumped to partial;
  brick diagram updated (orchestrator + tool system).
- `docs/decisions/README.md` — index updated.
- `docs/README.md` — tools doc linked.

### Tests

- 26 tests — decorator, registry, approval repo.
- 37 tests — three transformers, three approvers (including the
  order-pinning counter-example).
- 38 tests — workspace sandbox, SSRF validate / resolve /
  safe-fetch with mocked DNS and `httpx.MockTransport`.
- 58 tests — built-in tools (15 `file_read`, 12 `file_write`,
  15 `grep`, 16 `web_fetch`).
- **Total suite: 537 passing** (up from 479 on 0.4.0).

Ruff clean across `src/` and `tests/`. Pyright clean on `src/`;
the test-suite's pre-existing pyright warnings (union-narrowing
and mock-type issues) are unchanged.

### Deferred (to subsequent releases)

- `DefaultToolRunner` — per-call lifecycle driver that replaces
  `RaisingToolRunner`. Will drive the state machine (pending →
  approved → executing → terminal), apply the transformer chain,
  enforce `asyncio.timeout(tool.timeout_s)`, and persist approval
  decisions. Tracked in `.plan/tool-system-design.md` §7.
- `DelegationTool` — concrete Tool implementation for spawning
  sub-sessions against alternative models, with mid-stream cost
  caps and parent attribution. Tracked in
  `.plan/tool-system-design.md` §12.
- Orchestrator wiring — replacing `EmptyToolRegistry` +
  `RaisingToolRunner` with the real ones; `DelegationSpawned` /
  `DelegationCompleted` emission.
- IP pinning against DNS rebinding — V2; see ADR 0017.
- MCP client — V2. The `tool_kind="mcp"` discriminator and
  registry slot are already in place.

## [0.4.0] — 2026-04-20

### Added — Orchestrator (`src/cairn/orchestrator/`)

The pivotal brick. Coordinates providers, persistence, memory, tools,
context assembly, and the UI through an explicit per-turn state
machine plus four typed middleware seams.

- **`Orchestrator` class** — public API: `start_session`,
  `resume_session`, `archive_session`, `run_turn`, `cancel`,
  `resume_aborted_turns`.
- **Explicit per-turn state machine**: `STARTED → MEMORY_RETRIEVAL →
  ITERATION ↻ (TOOL_DISPATCH) → FINALISING → EXTRACTION_ENQUEUED →
  COMPLETED`, with `ABORTED` as the sole terminal failure state.
  Every transition is a conditional `UPDATE` against the new `turns`
  table; illegal transitions raise `InvalidTurnTransition`.
- **Collaborator protocols** — `ContextManager`, `MemoryService`,
  `ToolRegistry`, `ToolRunner`, `ExtractionQueue`, `ApprovalGateway`,
  `CostTracker`, `Tool`, `Clock`. Each ships with a no-op / stub
  implementation so the orchestrator is fully testable before the
  downstream bricks (tool system, memory, UI) exist.
- **Four middleware seams** with narrow typed contracts:
  `MessagePreparer` (rewrites requests), `ToolApprover` (gates tool
  calls), `ResultTransformer` (scrubs tool results),
  `UIEventObserver` (sync fan-out, fire-and-forget). Default chains
  are empty; bricks register their own middleware at
  harness-assembly time.
- **`SessionManager`** — thin coordinator over `SessionRepo`. Owns
  memory-space scoping per session type (ephemeral forced to None,
  companion defaulting to `companion`, persona honoured verbatim),
  model-ref resolution via `ModelRegistry`, ID generation.
- **`BasicCostTracker`** — wraps `UsageRepo` for writes, implements
  `should_block_turn` (`PROCEED | WARN | BLOCK`) against per-session
  and daily budget caps.
- **Cancellation** — per-session `asyncio.Event`; the turn loop
  checks between awaits. On cancel: partial assistant message
  persisted, turns row → `ABORTED`, `TurnAborted` emitted.
- **Crash recovery** via `resume_aborted_turns()` — scans non-terminal
  turns on boot and marks them aborted with `reason='process_crash'`.
- **New UIEvent types** — `ToolCallPlanned`, `ToolCallApproved`,
  `ToolCallRejected`, `TurnAborted`, `TurnBlocked`, `TurnIncomplete`,
  `BudgetWarning`. `turn_id` threaded through every per-turn event.

### Added — Persistence: `turns` + `approval_decisions`

Migration `0002_orchestrator_tables.sql`:

- **`turns` table** — one row per turn, `state` column authoritative,
  indexes on `(session_id, started_at)` and `state NOT IN (terminal)`.
- **`approval_decisions` table** — audit trail for tool-call approval
  decisions. Populated by the tool-system brick; schema lives here.
- **`turn_id` columns** on `messages`, `tool_calls`, `model_usage` —
  threading for cost-per-turn aggregations and clean debugging joins.
  Deliberately *not* foreign keys (see ADR 0012 for the circular
  write dependency that motivated the decision).
- **`TurnRepo`** — conditional state transitions, iteration counter,
  mark-completed/aborted, list-non-terminal for crash recovery.
- **`UsageRepo.record`** now accepts an optional `turn_id` kwarg;
  `UsageRecord` carries it. New `UsageRepo.cost_for_turn(turn_id)`
  aggregation.
- **`MessageRepo.append`** accepts an optional `turn_id` kwarg.

### Added — New domain type

- **`MemoryEntry`** — frozen Pydantic model for retrieved memory
  observations. Placed in `cairn.domain` so the orchestrator and
  context manager can be written against it before the memory brick
  lands.

### Added — User documentation

- `docs/orchestrator.md` — user-facing surface and lifecycle.
- `docs/decisions/0009-protocol-stubs.md` — why the orchestrator is
  built on injected protocols with default stubs rather than concrete
  collaborators.
- `docs/decisions/0010-typed-middleware.md` — why four narrow
  middleware seams instead of a generic callback system.
- `docs/decisions/0011-explicit-turn-state-machine.md` — why the
  `turns` table uses a state column rather than event sourcing.
- `docs/decisions/0012-no-fk-on-turn-id.md` — why `turn_id` is not a
  SQL foreign key.
- `docs/architecture.md` — orchestrator status bumped to Shipped.
- `docs/decisions/README.md` — index updated.

### Tests

- 15 orchestrator scenarios (happy path, tool dispatch, rejected
  tool, unknown tool, budget block, single-turn invariant,
  cancellation, memory-space threading, ephemeral behaviour, crash
  recovery, session-lifecycle events).
- 10 `TurnRepo` tests.
- 10 `BasicCostTracker` tests.
- 16 `SessionManager` tests.
- Pre-existing tests updated for the new required `turn_id` field on
  UIEvents and the second migration.
- **Total suite: 378 passing** (up from 353 on 0.3.1).

### Deferred (to subsequent bricks)

- Real `ToolRunner` implementation (tool-system brick) — V1 ships
  `RaisingToolRunner` paired with `EmptyToolRegistry`.
- Real `MemoryService` + observation extraction (memory brick) — V1
  ships `NullMemoryService` and `NullExtractionQueue`.
- Real `ApprovalGateway` with UI interaction (tool-system brick) — V1
  ships `AutoApproveGateway` / `DenyAllGateway`.
- Context-manager features beyond pass-through: compaction, memory
  injection, convention-file loading, prompt-cache markers.
- Per-tool timeout enforcement (lives in the tool-runner).
- `DelegationSpawned` / `DelegationCompleted` event emission from
  inside `DelegationTool.invoke` (tool-system brick).
- `/context` inspection metadata (context-manager brick).

## [0.3.1] — 2026-04-20

### Added — User documentation

Backfill of user-facing documentation in `docs/` covering the four
already-shipped bricks:

- `docs/architecture.md` — system shape, brick boundaries, and the data
  flow of a companion turn.
- `docs/configuration.md` — full TOML reference: every key across
  `[providers.*]`, `[[models]]`, `[profiles.*]`, `budgets`,
  `convention_files`, and `delegation_tools`, plus the four `SecretRef`
  schemes and the three-tier merge rules.
- `docs/providers.md` — per-provider notes (Anthropic, OpenAI including
  OpenAI-compatible local servers, OpenRouter), streaming and tool-call
  semantics, error hierarchy.
- `docs/persistence.md` — per-profile SQLite layout, WAL specifics,
  table overview, memory-space enforcement, and the backup story.

### Added — Architecture Decision Records

Eight ADRs in `docs/decisions/` capturing the non-obvious decisions that
shaped the shipped bricks, using Michael Nygard's *Context / Decision /
Consequences* format:

- 0001 — Per-profile SQLite database.
- 0002 — Three-tier config merge (user / project / local).
- 0003 — `SecretRef` with four schemes (`keyring`, `env`, `prompt`,
  `literal`).
- 0004 — Hand-rolled SQL migrations over an ORM / framework.
- 0005 — Per-provider adapters over a unified client.
- 0006 — Role-based model selection.
- 0007 — Memory-space scoping enforced at the repository layer.
- 0008 — Tool-call state as a column, not an event stream.

### Changed

- `README.md` now links into `docs/` for discoverability.

### Policy

Documentation is now a first-class citizen: every feature PR updates
the relevant user docs, and any non-obvious design decision lands with
an ADR in the same PR as the code. Recorded in `docs/README.md` and
(locally) in `AGENTS.md`.

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

[Unreleased]: https://github.com/wiktordepina/cairn/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/wiktordepina/cairn/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/wiktordepina/cairn/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/wiktordepina/cairn/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/wiktordepina/cairn/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/wiktordepina/cairn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/wiktordepina/cairn/releases/tag/v0.1.0
