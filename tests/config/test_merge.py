"""Tests for deep_merge — the config merging logic."""

from __future__ import annotations

from cairn.config._merge import deep_merge


class TestDeepMerge:
    def test_scalar_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_dict_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        assert deep_merge(base, override) == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_array_replaces_not_concatenates(self) -> None:
        base = {"tags": [1, 2, 3]}
        override = {"tags": [4, 5]}
        assert deep_merge(base, override) == {"tags": [4, 5]}

    def test_none_in_override_does_not_erase(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"a": None}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_new_keys_added(self) -> None:
        base = {"a": 1}
        override = {"b": 2}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_empty_override(self) -> None:
        base = {"a": 1, "b": {"c": 2}}
        assert deep_merge(base, {}) == base

    def test_empty_base(self) -> None:
        override = {"a": 1, "b": {"c": 2}}
        assert deep_merge({}, override) == override

    def test_three_tier_chain(self) -> None:
        user = {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}
        project = {"b": {"y": 25, "z": 30}}
        local = {"a": 99}
        result = deep_merge(deep_merge(user, project), local)
        assert result == {"a": 99, "b": {"x": 10, "y": 25, "z": 30}, "c": 3}

    def test_deeply_nested_merge(self) -> None:
        base = {"a": {"b": {"c": {"d": 1}}}}
        override = {"a": {"b": {"c": {"e": 2}}}}
        assert deep_merge(base, override) == {"a": {"b": {"c": {"d": 1, "e": 2}}}}

    def test_override_dict_with_scalar(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": "flat"}
        assert deep_merge(base, override) == {"a": "flat"}

    def test_override_scalar_with_dict(self) -> None:
        base = {"a": "flat"}
        override = {"a": {"b": 1}}
        assert deep_merge(base, override) == {"a": {"b": 1}}

    def test_does_not_mutate_inputs(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"c": 2}}
