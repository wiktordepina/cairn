# 0019 — Docstrings follow Google style

**Date:** 2026-04-21
**Status:** accepted

## Context

[ADR 0018](0018-docs-site-tooling.md) selected MkDocs + Material +
`mkdocstrings[python]` as cairn's documentation stack and, in passing,
called out that the docstring style would be Google. That ADR is
primarily about *build and publication* tooling — the authoring
convention deserves its own record so it survives a tooling change and
so it's discoverable by anyone searching ADRs for "docstrings".

The practical state at the time of this ADR: most docstrings in
`src/cairn/` are plain prose. A minority use RST-style double-backticks
(`` ``Clock.now()`` ``) for inline code — a habit from Sphinx-heavy
Python projects. Nine docstrings across the tree use Google-style
section headings. Contributors have no single style to follow, which
will produce drift as the codebase grows and now that docstrings
render on a user-visible site.

`mkdocstrings[python]` supports Google, numpy, and sphinx (reST) styles
via its handler config. Only one can be the house style — mixing
produces inconsistent rendering and confuses contributors.

## Decision

**All cairn docstrings follow Google style.** The `docstring_style:
google` option in `mkdocs.yml` is the canonical configuration; this
ADR records the authoring convention that config implies.

Concretely:

- **One-line summary** as the first line, ending with a period. Blank
  line, then any longer explanation.
- **Structured sections** — `Args:`, `Returns:`, `Raises:`, `Yields:`,
  `Examples:` — used when they clarify. Not required for every
  docstring. A simple getter with a self-evident return type is fine
  as a one-liner. A method taking three parameters with non-obvious
  failure modes should have sections.
- **Inline code uses single backticks** — `` `foo` ``. Never
  `` ``foo`` ``. RST-style double-backticks render literally in
  Markdown-mode mkdocstrings output.
- **British English** — consistent with the rest of cairn's
  user-facing text.
- **Module docstrings** may remain a single sentence. There's no
  requirement to promote every module header into full sections.
- **Private symbols** (leading underscore) may have terser docstrings
  since they don't appear on the published site; contributors still
  benefit from a one-line summary.

### Example — good

```python
def resolve_hostname(host: str, *, timeout_s: float = 5.0) -> list[IPv4Address]:
    """Resolve a hostname to its public IP addresses.

    Filters out loopback, RFC1918, link-local, and cloud-metadata
    ranges before returning. Used by SSRF defence before every
    outbound HTTP connection.

    Args:
        host: Hostname to resolve. Must not include scheme or port.
        timeout_s: DNS lookup timeout in seconds.

    Returns:
        The list of public IPv4 addresses the hostname resolves to.
        Empty if every resolved address is in a blocked range.

    Raises:
        SSRFBlocked: If `host` itself is an IP literal in a blocked
            range (pre-DNS check).
        socket.gaierror: If DNS resolution fails or times out.
    """
```

### Example — avoid

```python
def resolve_hostname(host, timeout_s=5.0):
    """Resolves ``host`` to public IPs, filtering blocked ranges.

    Raises ``SSRFBlocked`` for IP literals.
    """
```

Problems: double-backticks render literally on the site; no structured
sections for a function that takes parameters and raises specific
exceptions; first line leads with "Resolves" rather than a clean
imperative "Resolve".

## Consequences

**Easier:**

- One style across the tree. New contributors follow the same rule
  whether they're writing a tool, a provider adapter, or an
  orchestrator middleware.
- `mkdocstrings` renders structured sections as proper parameter and
  return blocks on the reference pages, which is a meaningful lift
  over plain-prose rendering.
- Any future lint gate (e.g. `ruff`'s pydocstyle rules with
  `convention = "google"`) has a single target to enforce against.

**Harder:**

- PRs touching public symbols are expected to update the docstring in
  the same change. Reviewers will flag mismatches. This is a small
  tax on velocity.
- Style consistency is a review concern until a lint rule lands. No
  automation catches a new RST-style double-backtick today.

**Ongoing cost:**

- Existing plain-prose docstrings aren't a bug — they're acceptable
  under this convention. Backfilling them with sections is editorial
  work that happens organically as code gets touched, not as a big-bang
  rewrite. The four reference pages in `docs/reference/` are where
  this polish matters most; elsewhere it's lower priority.
- A ripple to future ADRs: if cairn ever moves off `mkdocstrings`, the
  convention either stays (because Google style is the most widely
  recognised and readable of the three options) or gets re-examined
  against the new tool's preference.

## Alternatives considered

- **numpy style.** Common in scientific Python. More verbose than
  Google (section headings are underlined with dashes, parameters use
  a specific two-line layout). Google style reads better in a codebase
  where functions typically take 1–3 parameters.
- **sphinx / reST style** (`:param foo:`, `:returns:`). Reads poorly
  in source; the directive syntax belongs in RST files, not Python
  docstrings. Would also conflict visually with the Markdown-heavy
  narrative docs.
- **No convention, plain prose only.** The state before this ADR. No
  way to render a parameter table; no lint path; drift is inevitable
  as the codebase grows.
