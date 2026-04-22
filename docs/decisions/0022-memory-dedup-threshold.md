# 0022 — Dedup observations via `SequenceMatcher.ratio() >= 0.90`

**Date:** 2026-04-22
**Status:** accepted

## Context

Observations surface repeatedly across sessions. Without dedup, the
same fact re-observed ten times produces ten rows, noise drowns out
signal in retrieval, and the JSONL log balloons. Mait-code hit this
problem and landed on a `difflib.SequenceMatcher.ratio()`-based
dedup path that compares new content against recent FTS5 candidates
of the same space and type.

V1 cairn adopts the same approach. Threshold choice and scope need
to be documented so future changes are informed.

## Decision

`MemoryRepo.store()` runs this flow inside a `BEGIN IMMEDIATE`
transaction:

1. Extract up to 8 significant words from the new content
   (lowercase, stopword-filtered).
2. FTS5 `MATCH` query, OR-joined over those words, filtered by
   `memory_space` + `entry_type`, `LIMIT 20`.
3. Compute `SequenceMatcher(None, new_content, candidate).ratio()`
   for each candidate.
4. If `ratio >= 0.90`: UPDATE the existing row's `updated_at` and
   `importance = MAX(existing, new)`; return the refreshed row.
5. Otherwise: INSERT the new row.

**Threshold: 0.90.** Chosen to match mait-code's empirically-tuned
value. 0.90 catches near-duplicates (one-word changes, synonym
swaps of short content) without false-positives on semantically
distinct but lexically overlapping observations.

**Scope: same space AND same entry type.** Different spaces are
always distinct by invariant. Different entry types (e.g. fact vs
preference) are considered distinct because their downstream
handling differs — a fact and a preference about the same topic
aren't duplicates even if their text happens to overlap.

## Consequences

- **Positive.** Same fact re-observed collapses to one row with a
  refreshed timestamp — recency correctly reflects the latest
  observation.
- **Positive.** The `BEGIN IMMEDIATE` ensures two concurrent
  extractors can't race-insert near-duplicates.
- **Negative.** Semantic near-duplicates with lexically different
  phrasings ("User loves keto" vs "User is on a ketogenic diet")
  produce two rows. V3 adds vector search; at that point dedup can
  upgrade to a cosine-similarity gate.
- **Negative.** `SequenceMatcher` is O(N*M). With 20 candidates × ~200
  char content each, runtime is negligible today; at 100k+ entries
  per space we'd want to revisit.

## Alternatives considered

**Exact-string dedup (`content = ?` lookup).** Zero overhead, but
misses near-duplicates — the common case is a rephrasing, not a
verbatim match.

**Content hashing (SHA-256).** Same shortcoming as exact-string:
hashes aren't fuzzy.

**Embeddings-based dedup.** Robust against rephrasing but requires a
model, a vector column, and an embedding provider. Deferred to V3.

**Threshold at 0.95.** Tried during design; too strict — it let
through "User prefers Python" and "User prefers python" as
distinct (punctuation/casing differences can drop the ratio below
0.95 on short inputs).

**Threshold at 0.80.** Too lenient — overlapping but genuinely
different observations would merge and one would be lost.
