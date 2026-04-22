"""Tests for `cairn.persistence._text` — stopword-filtered tokeniser
used by the memory repo's dedup path.
"""

from __future__ import annotations

from cairn.persistence._text import significant_words


class TestSignificantWords:
    def test_strips_stopwords(self) -> None:
        assert significant_words("the quick brown fox") == ["quick", "brown", "fox"]

    def test_lowercases(self) -> None:
        assert significant_words("Python SCRIPTING Tasks") == [
            "python",
            "scripting",
            "tasks",
        ]

    def test_skips_pure_numeric(self) -> None:
        assert significant_words("user ran 123 times") == ["user", "ran", "times"]

    def test_keeps_mixed_alphanumeric(self) -> None:
        assert significant_words("Python3 and Go1_22 work") == ["python3", "go1_22", "work"]

    def test_limit_applied(self) -> None:
        text = "one two three four five six seven eight nine ten"
        # All of these are non-stopwords → we should cut at 8.
        out = significant_words(text)
        assert len(out) == 8
        assert out[:3] == ["one", "two", "three"]

    def test_preserves_order_with_duplicates(self) -> None:
        # "python" appears twice; both should be kept because the caller
        # decides on dedup.
        assert significant_words("python rocks python rules") == [
            "python",
            "rocks",
            "python",
            "rules",
        ]

    def test_empty_text(self) -> None:
        assert significant_words("") == []

    def test_all_stopwords(self) -> None:
        assert significant_words("to the a it is with") == []

    def test_punctuation_split(self) -> None:
        assert significant_words("hello, world! keto-diet.") == [
            "hello",
            "world",
            "keto-diet",
        ]
