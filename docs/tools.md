# Tools

Tools are the surface the model reaches through to affect the world.
Reading a file, writing a file, fetching a URL, searching for a regex,
delegating to another model — all of these flow through the tool system.

This is the highest-stakes surface in cairn. Every design choice here is
made with the blast radius of a misbehaving model in mind: scoped
filesystem access, SSRF-defended URL fetching, tiered approval, per-hop
validation on redirects, secret redaction on outputs, spotlighting to
label content as untrusted. For *why* specific choices were made, see
the ADRs linked throughout.

## Shape of a tool

Every tool conforms to a narrow protocol:

```python
@runtime_checkable
class Tool(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def input_schema(self) -> dict[str, object]: ...
    @property
    def tool_kind(self) -> Literal["native", "mcp", "delegation"]: ...
    @property
    def approval_required(self) -> bool: ...
    @property
    def risk_tier(self) -> int: ...           # 0-4 in V1
    @property
    def side_effects(self) -> Literal["none", "read", "write"]: ...
    @property
    def timeout_s(self) -> float: ...

    async def invoke(
        self,
        args: dict[str, object],
        ctx: TurnContext,
    ) -> ToolResultBlock: ...
```

A tool advertises its risk tier, side-effect class, and wall-clock
timeout. The registry uses this metadata for scoping; the approver
chain uses it for gating; the runner uses it for lifecycle accounting.

## Risk tiers

Every tool declares a risk tier 0-4. Tier 5+ (unsandboxed code
execution) is not supported in V1 — see [ADR 0016](decisions/0016-no-shell-in-v1.md).

| Tier | Shape | Default approval | Examples |
|---|---|---|---|
| 0 | Pure compute, no I/O | auto-approve | hypothetical `calculator` |
| 1 | Scoped filesystem read | auto-approve | `file_read`, `grep` |
| 2 | External read (network egress) | auto-approve with visible log | `web_fetch` |
| 3 | Mutating — scoped filesystem, or spending (delegation) | first-run prompt, then session allowlist | `file_write`, `DelegationTool` |
| 4 | External side effects (email, posts, HTTP side-effects) | always prompts | *none in V1* |

The decorator enforces valid combinations: tiers 0-2 must be read-only
(`side_effects` in `{"none", "read"}`), tier 3+ must not claim
`side_effects="none"`. See [ADR 0013](decisions/0013-tier-taxonomy.md)
for the full rationale.

## Session-type scoping

`DefaultToolRegistry` enforces which tools each session type can see:

| Session type | Default | Override |
|---|---|---|
| `COMPANION` | Full set (companion tools + MCP tools) | — |
| `PERSONA` | `persona_allowlists[session.persona]` (default `[]`) | per-persona config |
| `EPHEMERAL` | `[]` | `ephemeral_allowlist` by name |

Delegation sub-sessions are created as `EPHEMERAL` with no override —
they see no tools. Memory-space isolation is the companion principle;
tool-visibility scoping is this principle.

Duplicate tool names across the companion and MCP sets are a
configuration error — the registry raises `ValueError` at
construction.

## Registering a tool — the `@tool` decorator

The ergonomic path is a decorated async function:

```python
from pydantic import BaseModel, Field
from cairn.tools import tool

class EchoArgs(BaseModel):
    message: str = Field(description="Text to echo.")

@tool(
    name="echo",
    description="Echo a message back.",
    risk_tier=0,
    side_effects="none",
    timeout_s=5.0,
    args_model=EchoArgs,
)
async def echo(args: EchoArgs, ctx: TurnContext) -> str:
    return args.message
```

The decorator:

1. Validates `risk_tier` ∈ {0, 1, 2, 3, 4} and that the tier / side-effects
   combination is consistent.
2. Generates the JSON `input_schema` from `args_model.model_json_schema()`.
3. Defaults `approval_required` from the tier (`True` if tier ≥ 3).
4. Wraps the function in a `Tool` protocol-conforming instance.

Return type can be a plain `str` or a `list[ContentBlock]` for richer
output. The decorator wraps the return in a `ToolResultBlock` with
`is_error=False` and a blank `tool_use_id` — the runner stamps the
real call id on the way back. Tool authors do not need to know or
thread through the call id. Raising propagates to the runner, which
translates expected failures (`ToolError` and subclasses) into error
result blocks.

Tools are **not** auto-registered as a side effect of import. The CLI
imports tool modules and builds the registry explicitly — see
[ADR 0014](decisions/0014-tool-decorator.md).

## Security primitives

Two modules under `cairn.tools.security` give filesystem and URL-fetch
tools a common safety floor. Both are used by the V1 built-in tools
and are available to any custom tool.

### Workspace sandbox

```python
from cairn.tools.security import WorkspaceSandbox

sandbox = WorkspaceSandbox(root=repo_root)
safe_path = sandbox.resolve("src/main.py")   # Path, inside sandbox
sandbox.resolve("/etc/passwd")                # raises PathEscape
sandbox.resolve("../../etc/passwd")           # raises PathEscape
sandbox.resolve("./symlink-to-outside")       # raises PathEscape
```

Rules:

- Reject absolute paths (caller must pass relative).
- Reject any input that resolves outside `root` after `..` collapse.
- Reject symlinks that point outside `root`.

`resolve()` does not require the target to exist — callers enforce
existence at the file-op layer. `assert_readable` / `assert_writable`
are belt-and-braces checks for paths constructed by other means.

### SSRF defence

`cairn.tools.security._ssrf` is the shared front-end for any tool that
fetches a URL.

```python
from cairn.tools.security import validate_url, resolve_hostname, safe_fetch

host, url = validate_url("https://example.com/")   # scheme + hostname checks
resolve_hostname(host)                              # raises if any resolved IP is private
body = await safe_fetch("https://example.com/")     # full pipeline; returns bytes
```

What it guards against:

- **Scheme escapes** — only `http` and `https` are allowed. `file://`,
  `gopher://`, `data:`, `javascript:` all raise `SSRFBlocked`.
- **Private-network pivots** — every resolved IP is checked against a
  list of blocked ranges (loopback, RFC1918 private, link-local incl.
  cloud-metadata `169.254.169.254`, IPv6 ULA / link-local / unspecified,
  carrier-grade NAT). A single blocked IP in the resolution set fails
  the call — DNS rebinding can't shuffle past a one-strike rule.
- **Embedded credentials** — URLs with `user:pass@host` are rejected
  outright.
- **Redirect surprises** — `safe_fetch` sets `follow_redirects=False`.
  Tools that need to follow redirects re-enter the validation for every
  hop (see `web_fetch`).
- **Oversized responses** — streaming reads cap at `max_size` bytes.

**V1 limitation:** IP pinning (connecting to the validated IP while
preserving the Host/SNI header) is deferred. Clean HTTPS pinning
requires a custom SSL context and the practical threat (DNS rebinding)
needs attacker-controlled DNS. See [ADR
0017](decisions/0017-ssrf-ip-pinning-deferred.md).

## Security middleware

The tool system ships three `ResultTransformer`s and three
`ToolApprover`s registered into the orchestrator's middleware chains
at harness assembly. Defaults are conservative; the CLI can reorder or
supplement them.

### Result transformers

Applied in this order to every tool result before it's appended to the
session and sent back to the model:

1. **`InvisibleUnicodeStripper`** — removes zero-width, bidi-override,
   bidi-isolate, and Unicode-Tag-plane codepoints. Runs first so a
   malicious tool output cannot split an API-key across regex boundaries
   with an invisible separator.
2. **`SecretRedactor`** — regex-based replacement for common token
   shapes: `Bearer <…>`, `sk-…`, `sk-ant-…`, `AIza…`, `AKIA…`.
3. **`SpotlightTransformer`** — wraps the result in a
   `<tool_result tool="…" trust="untrusted">…</tool_result>` envelope
   with a plain-English warning. A nudge, not a hard security control —
   current models comply less often with injections inside the
   envelope, but determined injections still bypass it.

The strip-first ordering is pinned by a counter-example test so it
can't regress silently.

### Approvers

Run in this order against each Tier ≥ 2 tool call; first non-ESCALATE
wins. If the chain exhausts, the terminal `ApprovalGateway` (typically
UI-driven) decides.

1. **`AutoApproveReadOnly`** — approves calls with `side_effects` in
   `{"none", "read"}` AND `risk_tier ≤ 2`.
2. **`SessionAllowlist`** — exact-match `(tool_name, args_signature)`
   cache populated on user approval. Re-prompts on any argument change;
   see [ADR 0015](decisions/0015-session-allowlist-exact-match.md).
3. **`TierGate`** — escalates every Tier ≥ 4 call unconditionally as
   a belt-and-braces check against a buggy earlier approver.

## Built-in tools (V1)

Four tools ship in V1. Each exposes a `make_<tool>(…)` factory rather
than a module-global instance, so the CLI can bind them to whichever
workspace root is in scope.

### `file_read` — Tier 1

```python
from cairn.tools.builtin import make_file_read

file_read = make_file_read(sandbox, max_bytes=1_000_000)
```

| Arg | Type | Description |
|---|---|---|
| `path` | `str` | Path relative to the workspace root. |

- Paths resolve through `WorkspaceSandbox` — no absolute paths, no
  escapes, no symlink chasing out of the workspace.
- Files over `max_bytes` (default 1 MB) are truncated with a
  `... [truncated: N bytes total]` suffix.
- Binary files are not returned as text — the response is a summary
  of the form `[binary: <mime>, <size> bytes, sha256:<prefix>]`.
  Detection is a null-byte probe on the first 8 KB.

### `file_write` — Tier 3 (approval required)

```python
file_write = make_file_write(sandbox, max_bytes=1_000_000)
```

| Arg | Type | Description |
|---|---|---|
| `path` | `str` | Path relative to the workspace root. |
| `content` | `str` | UTF-8 text to write. |
| `mode` | `"create" \| "overwrite" \| "append"` | Default `"overwrite"`. |

- `create` fails if the target exists; `overwrite` replaces it;
  `append` extends it.
- Parent directory must exist — the tool does not `mkdir`.
- Writes over `max_bytes` are rejected before any disk I/O.
- Non-regular-file targets are refused in `overwrite` and `append`
  modes.
- First-run approval flow: the approval chain prompts the first time
  the model writes to a given path; the `SessionAllowlist` approver
  auto-approves subsequent writes to the *same path* (exact args
  match). Writes to a different path re-prompt.

### `grep` — Tier 1

```python
grep = make_grep(sandbox, max_matches=200, max_file_bytes=1_000_000)
```

| Arg | Type | Description |
|---|---|---|
| `pattern` | `str` | Python-flavoured regex. |
| `path` | `str` | Path relative to the workspace root. Defaults to `.`. |
| `glob` | `str \| None` | Optional filename glob, e.g. `"*.py"`. |

- Search is scoped to the workspace sandbox.
- Hidden directories (`.*`), binary files (null-byte probe), and files
  over `max_file_bytes` are skipped.
- Output is `path:lineno:line`, one match per line, capped at
  `max_matches` with a `... [truncated: match cap N reached]` suffix.
- Uses ripgrep when `rg` is on `PATH` (argv-form invocation with
  `--no-follow`, `--max-filesize`, `--max-count`); falls back to a
  pure-Python walker on `re` + `pathlib.rglob` otherwise. Both paths
  produce the same output format.

### `web_fetch` — Tier 2

```python
from cairn.tools.builtin import make_web_fetch

web_fetch = make_web_fetch(
    max_bytes=5_000_000,
    timeout_s=30.0,
    max_redirects=5,
)
```

| Arg | Type | Description |
|---|---|---|
| `url` | `str` | Fully-qualified `http(s)` URL. |

- Runs the full SSRF defence at every hop, including each redirect
  target. Redirect chains are walked manually — `follow_redirects` is
  off at the HTTP-client level.
- Content-Type filtering: `text/*`, `application/json`,
  `application/xml`, and `application/javascript` variants return as
  text; anything else returns the same `[binary: <mime>, <size> bytes,
  sha256:<prefix>]` summary as `file_read`.
- HTML is best-effort stripped to plain text: `<script>` and `<style>`
  bodies are removed, remaining tags are dropped, entities are
  unescaped, whitespace is collapsed.
- 4xx / 5xx responses surface as `ToolError` — the model sees the
  failure and can decide how to proceed.

## Custom tools

To register a custom tool, write a decorated function and pass it to
the registry at harness-assembly time:

```python
from cairn.tools import DefaultToolRegistry, tool
from cairn.tools.builtin import make_file_read, make_file_write, make_grep, make_web_fetch
from cairn.tools.security import WorkspaceSandbox

sandbox = WorkspaceSandbox(root=workspace_root)

registry = DefaultToolRegistry(
    companion_tools=[
        make_file_read(sandbox),
        make_file_write(sandbox),
        make_grep(sandbox),
        make_web_fetch(),
        my_custom_tool,     # @tool-decorated async function
    ],
    persona_allowlists={
        "work-assistant": ["file_read", "grep", "web_fetch"],
    },
    ephemeral_allowlist=[],   # default; ephemeral sessions see no tools
)
```

Two things to keep in mind when writing a custom tool:

- **Raise `ToolError` for expected failures** (bad input, missing
  resource). The runner translates these into error result blocks the
  model can see. Unexpected exceptions are logged and surfaced as
  generic failures.
- **Go through the sandbox for any filesystem work**. If your tool
  takes a path from the model, `sandbox.resolve(path)` is the only
  correct way to turn it into a `Path` — it enforces the
  no-escape rule.

## What's shipped and what's not

As of 0.6.0 the tool-system brick is feature-complete for V1:

- ✅ `@tool` decorator, `DefaultToolRegistry`, `ApprovalDecisionRepo` (0.5.0).
- ✅ Security middleware — three transformers, three approvers (0.5.0).
- ✅ Workspace sandbox, SSRF defence (0.5.0).
- ✅ Four built-in tools — `file_read`, `file_write`, `grep`,
  `web_fetch` (0.5.0).
- ✅ **`DefaultToolRunner`** (0.6.0) — the real per-call lifecycle
  driver replacing the orchestrator's `RaisingToolRunner` stub.
  Writes `tool_calls` + `approval_decisions` rows, wraps
  `tool.invoke` in `asyncio.timeout(tool.timeout_s)`, classifies
  errors (`PathEscape` / `SSRFBlocked` / `ToolTimeout` / `ToolError`
  → USER; anything else → UNEXPECTED), and stamps `tool_use_id` on
  successful results where the tool left it blank. The orchestrator
  still owns the approval chain, UI event emission, and the
  `ResultTransformer` chain.
- ✅ **`DelegationTool`** (0.6.0) — the concrete tool for spawning
  ephemeral sub-sessions against alternative models, with mid-stream
  cost caps (`max_cost_usd`) and parent attribution via
  `parent_session_id`. The sub-session is archived in a `finally:`
  block so clean-up survives mid-stream failures.
- ✅ **Orchestrator wiring** (0.6.0) — `_dispatch_tools` now calls the
  runner once per tool call on both approve and reject paths. See
  [orchestrator.md](orchestrator.md) for the responsibility split.

Deferred to later releases:

- **`DelegationSpawned` / `DelegationCompleted` UI events.** The
  domain events exist and `DelegationTool` runs, but the orchestrator
  cannot emit them yet — the sub-session ID is created inside
  `DelegationTool.invoke()` and the `Tool` protocol has no
  back-channel to report it. Users can still observe delegation via
  `model_usage` rows where `operation = delegation` and via
  `SessionRepo.children_of(parent)`.
- **MCP client** — V2. The `tool_kind` discriminator and registry
  slot are already in place.

## Related documents

- [Architecture](architecture.md) — where the tool system sits in
  cairn.
- [Orchestrator](orchestrator.md) — the turn loop that drives tool
  calls.
- [ADR 0013](decisions/0013-tier-taxonomy.md) — the tier taxonomy.
- [ADR 0014](decisions/0014-tool-decorator.md) — why a decorator
  rather than a class hierarchy.
- [ADR 0015](decisions/0015-session-allowlist-exact-match.md) —
  exact-match args signature for session approvals.
- [ADR 0016](decisions/0016-no-shell-in-v1.md) — why V1 does not
  ship a shell tool.
- [ADR 0017](decisions/0017-ssrf-ip-pinning-deferred.md) — why
  IP pinning is not in V1.
