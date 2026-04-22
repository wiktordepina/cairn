"""Small text utilities used by the memory repo's dedup path.

Kept inside `persistence` so the dedup pipeline has no external deps
beyond the stdlib. The stopword list is deliberately tiny: the dedup
candidate query is coarse (FTS5 OR over significant words), and
`SequenceMatcher` does the real filtering. If observed dedup quality
drifts, revisit before reaching for a heavier NLP dep.
"""

from __future__ import annotations

import re

# ~40 most frequent English closed-class words. No proper-noun stripping,
# no stemming. The dedup path is forgiving by design.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at",
        "be", "been", "but", "by",
        "did", "do", "does",
        "for", "from",
        "had", "has", "have", "he", "her", "his",
        "i", "if", "in", "is", "it", "its",
        "me", "my",
        "not",
        "of", "on", "or", "our",
        "she", "so",
        "that", "the", "their", "them", "they", "this", "to",
        "was", "we", "were", "with",
        "you", "your",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'_-]*")


def significant_words(text: str, *, limit: int = 8) -> list[str]:
    """Return up to *limit* significant words from *text* in order.

    - Lower-cased.
    - Tokens must start with a letter (pure-numeric tokens skipped).
    - Stopwords removed.
    - Duplicates preserved in order; the caller decides whether to dedupe.

    Used by the memory-repo dedup path to build an FTS5 candidate query.
    """
    out: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0).lower()
        if token in _STOPWORDS:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out
