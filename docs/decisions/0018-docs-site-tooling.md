# 0018 — Documentation site tooling: MkDocs + Material + mkdocstrings

**Date:** 2026-04-21
**Status:** accepted

## Context

Cairn has committed to treating documentation as a first-class citizen: every
feature PR updates `docs/`, non-obvious decisions land with an ADR in the same
PR, and the `docs/` tree is intended to be authoritative. So far that tree has
been plain Markdown viewed through GitHub's file renderer. That is good enough
for contributors browsing the repo but falls short as user-facing
documentation: no search, no cross-document navigation, no version selector,
and no link between narrative docs and the public API surface exposed through
docstrings.

The goal for this decision is a documentation site that:

1. Renders `docs/*.md` (including ADRs under `docs/decisions/`) with
   first-class navigation, search, and theming.
2. Generates API reference pages from docstrings in `src/cairn/` without
   duplicating their contents into Markdown.
3. Publishes to GitHub Pages, with one site per release version plus a `dev`
   alias tracking `main`.
4. Integrates with the existing CI (GitHub Actions, `uv`, strict checks) and
   fails PRs on broken references.

The candidates considered were:

- **Sphinx** with MyST-Parser and `autodoc`. The textbook Python answer, but
  RST-first in its idioms even when authoring in Markdown. `autodoc` imports
  modules at build time — brittle for a project whose import graph includes
  `anthropic`, `openai`, `keyring`, `aiosqlite`. Theme choice (furo, shibuya,
  pydata, sphinx-rtd-theme) is load-bearing and configuration tends toward
  150-plus lines in `conf.py` for a project this size.
- **MkDocs + Material + mkdocstrings**. Markdown-first (matches the existing
  `docs/` tree). `mkdocstrings` with its Python handler uses Griffe for
  static AST analysis — no import required. Material is the de-facto theme
  with dark mode, instant search, admonitions, content tabs. Versioning is
  handled by `mike` as a first-class plugin.
- **pdoc**. Elegant zero-config API docs but no story for the narrative pages
  in `docs/` (architecture, configuration, providers, persistence, tools).
  Would force us to maintain two doc trees or skip narrative.
- **Quarto**. Geared at scientific / notebook-driven docs. Off-mission for a
  CLI application.
- **Status quo** (raw GitHub-rendered Markdown). No search, no theming, no
  versioning, no way to surface docstrings alongside prose.

Sphinx wins for pure API reference of a library with heavy intersphinx
cross-linking. Cairn is an application, not a library, and its docs read like
a user manual. MkDocs' model — narrative Markdown with `:::` directives
pulling docstrings into chosen places — matches how the docs are written.

A second constraint bit the publication flow. Versioning via `mike` writes a
layered site structure to a `gh-pages` branch; GitHub Pages has traditionally
served that branch directly. The newer Pages flow publishes via the
`actions/deploy-pages` action, which uploads an artefact with no branch
involvement and surfaces the deployment in the repo's Environments tab
(rollback, logs, deploy gates). The natural tension: mike needs a branch,
`deploy-pages` avoids one.

## Decision

**Stack.** MkDocs + Material for MkDocs + `mkdocstrings[python]` + `mike`.
Dependencies live in a `docs` dependency group in `pyproject.toml`, kept out
of the runtime install and the default `uv sync`.

**Authoring.**

- Narrative lives in `docs/*.md` as today.
- Curated API reference lives in `docs/reference/*.md`, four files — one per
  public surface layer: `providers`, `tools`, `orchestrator`, `domain`. Each
  is a short intro plus `:::` directives. No full-tree sweep; the reference
  mirrors the public API surface, not the module tree.
- ADRs are surfaced in the site nav under the Decisions tab.
- `docs/README.md` is the single landing file, used both as GitHub's
  folder-auto-rendered README and as the MkDocs home page via `nav`.

**Docstring style.** Google. Chosen because:

- Plain-prose docstrings in the codebase today map cleanly to Google-style
  sections as structure is added.
- Google style is the most widely recognised among Python developers.
- `mkdocstrings[python]` parses it natively with no extension required.

**Publication.** Hybrid `mike` → `actions/deploy-pages` flow. `mike`
maintains the version layout on the `gh-pages` branch; the deploy job checks
that branch out and publishes the site via `actions/deploy-pages@v4`.
Pages source is set to **GitHub Actions**, not "Deploy from a branch" — so
the Environments UI, deploy gates, and rollback remain available, and the
`gh-pages` branch is storage rather than an implicit publication surface.

**Versioning.** Aliases:

- `dev` — tracks `main`, redeployed on every push.
- `<version>` — deployed when a `v*` tag is pushed.
- `latest` — alias pointing at the newest released version; version selector
  defaults here.

**First-deploy bootstrap.** The deploy workflow includes a guarded step that
seeds `0.6.0` + `latest` + the default alias on the first run, so the site
goes from zero to a working versioned home automatically without a manual
bootstrap from a workstation.

**CI.** A `build` job runs `mkdocs build --strict` on every PR and push;
`--strict` fails the build on broken references, unresolved `:::` paths, and
nav orphans. A `deploy` job runs only on push to `main` or `v*` tag.

## Consequences

**Easier:**

- The existing `docs/` tree becomes the site source with no format
  conversion. ADRs, configuration reference, and architecture docs drop in
  as-is.
- Griffe's static analysis means the docs build needs no side-effectful
  imports — a clean separation between the runtime dependency graph and the
  docs build.
- Material's instant search covers both narrative prose and docstring
  content in one index.
- Contributors get live-reload via `uv run mkdocs serve` with no further
  setup beyond `uv sync --group docs`.
- Versioned docs via `mike` means users reading the site for an older
  release don't see documentation ahead of the code they're running.

**Harder:**

- `gh-pages` branch exists as mike's storage back-end. It isn't Pages' direct
  source, but it's a synthetic branch in `git branch -a` listings.
- Docstring drift is now user-visible. A stale or mis-formatted docstring
  that was previously a private implementation detail now renders on the
  public site.
- Docs become a CI gate. A PR that breaks a `:::` reference (by renaming a
  class, removing a public export, or mistyping a path) fails the strict
  build until the reference is fixed.
- The reference pages are hand-curated. When a new public symbol is added to
  `cairn.providers`, `cairn.tools`, `cairn.orchestrator`, or `cairn.domain`,
  someone must add a `:::` entry. This is captured in `AGENTS.md`; the
  alternative (auto-generated from `__all__`) was rejected as over-broad for
  a user-facing site.

**Ongoing cost:**

- The `docs` dependency group must track MkDocs, Material, `mkdocstrings`,
  and `mike` across their release cadences. These are active projects; minor
  version bumps occasionally change default behaviour (palette schemes, nav
  features, mkdocstrings handler options).
- Docstring quality becomes a review concern. British English, sentence-case,
  Google section headings (`Args`, `Returns`, `Raises`) — enforced by taste,
  not a linter, in V1.
- First release after this decision (`v0.7.0` onward) triggers the first
  real versioned publication. The `0.6.0` bootstrap happens on the first
  merge of this change to `main`.
