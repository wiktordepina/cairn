# 0020 — Auto-generated API reference from `__all__`

**Date:** 2026-04-22
**Status:** accepted
**Refines:** [ADR 0018](0018-docs-site-tooling.md) (does not supersede — 0018's
toolchain choice still stands; this ADR refines how the *reference*
section of that toolchain is authored).

## Context

[ADR 0018](0018-docs-site-tooling.md) picked MkDocs + Material for
MkDocs + `mkdocstrings[python]` + `mike` as cairn's documentation
stack. The published site ended up split between *narrative* pages
(`docs/architecture.md`, `docs/orchestrator.md`, etc. — hand-written
Markdown) and *reference* pages (`docs/reference/*.md` — hand-curated
Markdown containing one `:::` mkdocstrings directive per public
symbol).

In practice, the curated reference pattern produced two problems:

- **Coverage gaps.** Only four of cairn's six packages had reference
  pages — `config` and `persistence` were missing entirely, so
  `Database`, the five repos, `CairnConfig`, `SecretRef`, etc. were
  documented only via their docstrings in source, never reaching
  the published site.
- **Drift.** AGENTS.md asked contributors to remember to update
  `docs/reference/*.md` whenever a public symbol was added, renamed,
  or removed. There was no automated check; the responsibility was
  conventional only.

A natural fix is to auto-generate the reference pages from the
authoritative source — each package's `__all__`. That preserves the
editorial structure (the `# Section` comments inside `__all__` are
already grouped by role) without asking contributors to maintain a
parallel set of files.

The conventional MkDocs path for this — `mkdocs-gen-files` plus
`mkdocs-literate-nav` — was rejected after their 2026-03-16 releases
introduced a hard dependency on `properdocs`, a fork that injects
politicised warnings into every build. See "Alternatives considered"
below.

## Decision

**The reference pages under `docs/reference/` are generated from each
module's `__all__` by `docs/gen_ref_pages.py`.** Hand-edited
reference pages are removed; the generator owns that directory.

The generator:

- Lists modules to document in a `REFERENCE_MODULES` constant — both
  packages (`__init__.py`) and single-file modules (`logging.py`,
  `ssl.py`).
- Requires every listed module to declare `__all__`. A missing
  `__all__` is a hard error — the generator refuses to silently
  under-document.
- Parses `__all__` source line-by-line to recover the `# Section`
  comments interleaved with the symbol names. Those become H2
  headings in the generated page.
- Emits each `docs/reference/<module>.md` with one `:::
  cairn.<module>.<symbol>` directive per `__all__` entry, in
  source order, grouped by section.
- Also emits `docs/reference/index.md` as the section landing page.
- Supports `--check` mode which exits non-zero if any generated file
  differs from what's checked in.

**The generator is invoked manually** (`uv run python
docs/gen_ref_pages.py`) and its output is checked into the repo.
**CI runs `--check`** to detect drift; the same check is wrapped in a
pytest test (`tests/docs/test_gen_ref_pages.py`) so contributors get
the same guard locally before pushing.

The MkDocs nav for the reference section is hand-listed in
`mkdocs.yml`. With eight modules of stable identity, a dynamic nav
generator would add complexity for no readable benefit.

## Consequences

**Easier:**

- Coverage is automatic. Every package and single-file module listed
  in `REFERENCE_MODULES` is published; no symbol slips through
  because someone forgot to add a `:::` line.
- Editorial groupings live next to the code (the `# Section`
  comments in `__all__`), where the author of the symbol is best
  placed to decide grouping.
- Drift detection is a single pytest run. PRs that change `__all__`
  show the regenerated reference page in the same diff as the source
  change — reviewers see both halves of the change at once.

**Harder:**

- Contributors must remember to run the generator after editing
  `__all__`. The pytest drift check catches this before it lands;
  the friction is one extra command.
- Editorial structure is constrained to what fits in `__all__`
  comments. The previous hand-curated pages could mix ordering
  decisions and prose intros — the generator does only headings and
  directives.
- A new dependency on the source format: any contributor writing
  `__all__` differently (e.g. multi-line strings, computed
  expressions) breaks the parser. The error surfaces clearly, but
  it's a discipline.

**Ongoing cost:**

- The generator (~150 LOC) is now part of the project's surface and
  needs maintenance. It's small and self-contained, so the cost is
  low — but real.
- Adding a new module to the reference set is a one-line edit to
  `REFERENCE_MODULES` plus a regenerate. Removing one is the same.

## Alternatives considered

- **Hand-maintained `:::` pages with a CI freshness check.** Was the
  most likely-evolution path. The check would need to parse `__all__`
  on one side and the `:::` directives on the other, then diff. As
  much custom code as the generator, but without solving the
  coverage gap — every new module still needs a hand-authored page
  to exist before the check has anything to compare. Rejected
  because the same effort produces a stronger result if the pages
  are generated outright.
- **`mkdocs-gen-files` + `mkdocs-literate-nav`.** The conventional
  MkDocs auto-generation pattern. Both plugins added a hard
  dependency on `properdocs` on 2026-03-16, plus pinned
  `mkdocs<=1.6.1`. `properdocs` is a politicised fork that injects
  aggressive warnings about the upstream MkDocs maintainer into
  every build's stderr. Rejected on supply-chain hygiene grounds:
  AGENTS.md's "minimise transitive dependencies" rule applies, and
  pinning the last clean versions (`mkdocs-gen-files==0.6.0`,
  `mkdocs-literate-nav==0.6.2`) would leave us indefinitely on
  unmaintained code given the maintainer's stated direction. Both
  plugins are also small enough that we can do without them — the
  generator does the same job with `Path.write_text` and a static
  nav block in `mkdocs.yml`.
- **`mkdocstrings`'s package-level rendering.** A single
  `::: cairn.providers` directive renders the whole package without
  enumerating symbols. Rejected because it ignores `__all__`
  (renders whatever's importable) and flattens the section
  groupings. Loses both the curatorial signal and the privacy
  signal.
- **Pre-build hook in `mkdocs.yml`.** MkDocs supports `hooks:` in
  config, which would let the generator run as part of `mkdocs
  build`. Rejected in favour of an explicit script that contributors
  run themselves: PR diffs show the regenerated pages, which is
  better review UX than "trust the build" when a docstring changes.
