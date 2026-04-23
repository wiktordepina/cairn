"""Tests for the convention-file trust gate."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cairn.conventions import (
    AllowlistStore,
    AllowlistTrustGate,
    AlwaysTrustGate,
    DenyingPromptTrustGate,
    TrustDecision,
    default_allowlist_path,
    trust_gate_for_policy,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# AlwaysTrustGate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_always_gate_allows(tmp_path: Path) -> None:
    gate = AlwaysTrustGate()
    assert await gate.check(tmp_path, [tmp_path / "AGENTS.md"]) is TrustDecision.ALLOW


# ---------------------------------------------------------------------------
# AllowlistStore
# ---------------------------------------------------------------------------


def test_allowlist_add_creates_file(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "nested" / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    store.add(repo, now=datetime(2026, 4, 23, 10, 30, tzinfo=UTC))
    assert store.path.exists()
    content = store.path.read_text(encoding="utf-8")
    assert str(repo.resolve()) in content
    assert "2026-04-23T10:30:00Z" in content


def test_allowlist_add_is_idempotent(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    store.add(repo)
    store.add(repo)
    assert len(store.list_trusted()) == 1


def test_allowlist_remove_returns_status(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert store.remove(repo) is False
    store.add(repo)
    assert store.remove(repo) is True
    assert store.list_trusted() == []


def test_allowlist_contains_exact_and_descendant(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo = tmp_path / "repo"
    (repo / "sub" / "deep").mkdir(parents=True)
    store.add(repo)
    assert store.contains(repo) is True
    assert store.contains(repo / "sub" / "deep") is True
    other = tmp_path / "other"
    other.mkdir()
    assert store.contains(other) is False


def test_allowlist_parent_dir_uses_0o700(tmp_path: Path) -> None:
    store_dir = tmp_path / "config"
    store = AllowlistStore(store_dir / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    store.add(repo)
    mode = store_dir.stat().st_mode & 0o777
    assert mode == 0o700


def test_allowlist_list_trusted_empty_when_missing(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "absent.toml")
    assert store.list_trusted() == []


def test_allowlist_malformed_toml_is_backed_up(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "trusted_projects.toml"
    path.write_text("this is = not [[valid toml", encoding="utf-8")
    store = AllowlistStore(path)
    with caplog.at_level(logging.WARNING, logger="cairn.conventions._trust"):
        assert store.list_trusted() == []
    assert not path.exists()
    assert path.with_suffix(".toml.broken").exists()
    assert any("backing up" in rec.message for rec in caplog.records)


def test_allowlist_roundtrip_preserves_entries(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo_a = tmp_path / "a"
    repo_a.mkdir()
    repo_b = tmp_path / "b"
    repo_b.mkdir()
    store.add(repo_a, now=datetime(2026, 4, 1, tzinfo=UTC))
    store.add(repo_b, now=datetime(2026, 4, 2, tzinfo=UTC))
    reloaded = AllowlistStore(store.path).list_trusted()
    assert {e.path for e in reloaded} == {repo_a.resolve(), repo_b.resolve()}


# ---------------------------------------------------------------------------
# AllowlistTrustGate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_gate_allows_trusted(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    store.add(repo)
    gate = AllowlistTrustGate(store)
    assert await gate.check(repo, []) is TrustDecision.ALLOW


@pytest.mark.asyncio
async def test_allowlist_gate_denies_untrusted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = AllowlistStore(tmp_path / "trusted_projects.toml")
    repo = tmp_path / "repo"
    repo.mkdir()
    gate = AllowlistTrustGate(store)
    with caplog.at_level(logging.WARNING, logger="cairn.conventions._trust"):
        decision = await gate.check(repo, [])
    assert decision is TrustDecision.DENY
    assert any("allowlist" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# DenyingPromptTrustGate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_gate_denies_with_once_per_project_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = DenyingPromptTrustGate()
    repo_a = tmp_path / "a"
    repo_a.mkdir()
    repo_b = tmp_path / "b"
    repo_b.mkdir()
    with caplog.at_level(logging.WARNING, logger="cairn.conventions._trust"):
        assert await gate.check(repo_a, []) is TrustDecision.DENY
        assert await gate.check(repo_a, []) is TrustDecision.DENY  # no 2nd warn
        assert await gate.check(repo_b, []) is TrustDecision.DENY
    warns = [rec for rec in caplog.records if "UI brick" in rec.message]
    # One per distinct project.
    assert len(warns) == 2


# ---------------------------------------------------------------------------
# factory + default path
# ---------------------------------------------------------------------------


def test_default_allowlist_path_uses_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = default_allowlist_path()
    assert p == tmp_path / "xdg" / "cairn" / "trusted_projects.toml"


def test_default_allowlist_path_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("cairn.conventions._trust.Path.home", lambda: tmp_path)
    assert default_allowlist_path() == tmp_path / ".config" / "cairn" / "trusted_projects.toml"


def test_factory_dispatches(tmp_path: Path) -> None:
    store = AllowlistStore(tmp_path / "t.toml")
    assert isinstance(trust_gate_for_policy("always"), AlwaysTrustGate)
    assert isinstance(
        trust_gate_for_policy("project_allowlist", allowlist_store=store),
        AllowlistTrustGate,
    )
    assert isinstance(trust_gate_for_policy("prompt"), DenyingPromptTrustGate)
    with pytest.raises(ValueError, match="unknown trust_policy"):
        trust_gate_for_policy("bogus")  # type: ignore[arg-type]
