"""Tests for `cairn.watcher.Reloader` — surgical hot reload."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cairn.config import (
    ConfigError,
    ConventionFilesConfig,
)
from cairn.config._registry import ModelRegistry
from cairn.conventions import AllowlistStore, AllowlistTrustGate, ConventionLoader
from cairn.memory import ProfileDocLoader
from cairn.providers._registry import ProviderRegistry
from cairn.watcher import FileWatcher, Reloader, build_watch_set

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_user_config(home: Path) -> Path:
    """Write a minimal valid user-level config.toml under *home*."""
    cfg_dir = home / ".config" / "cairn"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(
        """\
schema_version = 1
active_profile = "default"

[providers.fake]

[[models]]
id = "test-primary"
provider = "fake"
display_name = "Test"
context_window = 100000
max_output_tokens = 4096
supports_tools = true
input_cost_per_1m = 1.0
output_cost_per_1m = 2.0
roles = ["primary", "utility"]

[profiles.default]
name = "default"
soul_document_path = "/tmp/soul.md"
user_context_path = "/tmp/user.md"
memory_md_path = "/tmp/MEMORY.md"
primary_model = "role:primary"
utility_model = "role:utility"
""",
        encoding="utf-8",
    )
    return cfg_path


def _make_loaders(repo: Path) -> tuple[ConventionLoader, ProfileDocLoader]:
    cfg = ConventionFilesConfig(enabled=True, filenames=["AGENTS.md"], user_level_paths=[])
    store = AllowlistStore(repo / "trusted.toml")
    loader = ConventionLoader(
        config=cfg,
        trust_gate=AllowlistTrustGate(store),
        cwd=repo,
    )
    docs = ProfileDocLoader(
        soul_document_path=repo / "soul.md",
        user_context_path=repo / "user_ctx.md",
        memory_md_path=repo / "MEMORY.md",
    )
    return loader, docs


def _make_orch_stub() -> object:
    class _OrchStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def replace_collaborators(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    return _OrchStub()


# ---------------------------------------------------------------------------
# Successful reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_swaps_collaborators_and_resnapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("HOME", str(home))
    _write_user_config(home)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    loader, docs = _make_loaders(repo)
    watcher = FileWatcher(
        watch_set=build_watch_set([]),
        observers=[],
        poll_interval_s=0.5,
    )
    orch = _make_orch_stub()

    reloader = Reloader(
        profile_name=None,
        orchestrator=orch,  # type: ignore[arg-type]
        convention_loader=loader,
        profile_doc_loader=docs,
        file_watcher=watcher,
        project_dir=repo,
    )

    # Prime the loader caches so we can verify they get invalidated.
    docs.load_soul_document()
    loader._cache = []  # type: ignore[attr-defined]  # short-circuit

    result = await reloader.reload()

    assert result.ok is True
    assert result.error is None
    assert result.summary  # non-empty banner copy

    # Collaborators were swapped.
    assert len(orch.calls) == 1  # type: ignore[attr-defined]
    swap = orch.calls[0]  # type: ignore[attr-defined]
    assert isinstance(swap["provider_registry"], ProviderRegistry)
    assert isinstance(swap["model_registry"], ModelRegistry)

    # Caches were dropped.
    assert loader._cache is None  # type: ignore[attr-defined]
    # Watcher was re-snapshotted (no entries since the test layout has
    # no convention or profile docs).
    assert watcher.watch_set.entries  # config layer at least


# ---------------------------------------------------------------------------
# Failed reload — validation error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_validation_failure_does_not_swap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    loader, docs = _make_loaders(repo)
    watcher = FileWatcher(
        watch_set=build_watch_set([]),
        observers=[],
        poll_interval_s=0.5,
    )
    orch = _make_orch_stub()
    reloader = Reloader(
        profile_name=None,
        orchestrator=orch,  # type: ignore[arg-type]
        convention_loader=loader,
        profile_doc_loader=docs,
        file_watcher=watcher,
        project_dir=repo,
    )

    with patch(
        "cairn.watcher._reloader.load_config",
        side_effect=ConfigError("boom: schema_version missing"),
    ):
        result = await reloader.reload()

    assert result.ok is False
    assert result.error is not None
    assert "boom" in result.error
    assert orch.calls == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Primary-model role-pin drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_detects_primary_model_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `role:primary` now resolves to a different model than the
    active session was created with, the result carries
    `primary_model_drift=(active_id, new_id)`. The session is not
    rebound — see ADR 0042."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("HOME", str(home))

    cfg_dir = home / ".config" / "cairn"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        """\
schema_version = 1
active_profile = "default"

[providers.fake]

[[models]]
id = "old-primary"
provider = "fake"
display_name = "Old"
context_window = 100000
max_output_tokens = 4096
supports_tools = true
input_cost_per_1m = 1.0
output_cost_per_1m = 2.0
roles = ["utility"]

[[models]]
id = "new-primary"
provider = "fake"
display_name = "New"
context_window = 100000
max_output_tokens = 4096
supports_tools = true
input_cost_per_1m = 1.0
output_cost_per_1m = 2.0
roles = ["primary"]

[profiles.default]
soul_document_path = "/tmp/soul.md"
user_context_path = "/tmp/user.md"
memory_md_path = "/tmp/MEMORY.md"
primary_model = "role:primary"
utility_model = "role:utility"
""",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    loader, docs = _make_loaders(repo)
    watcher = FileWatcher(
        watch_set=build_watch_set([]),
        observers=[],
        poll_interval_s=0.5,
    )
    reloader = Reloader(
        profile_name=None,
        orchestrator=_make_orch_stub(),  # type: ignore[arg-type]
        convention_loader=loader,
        profile_doc_loader=docs,
        file_watcher=watcher,
        project_dir=repo,
        # Active session was created when the previous role assignment
        # had primary on "old-primary"; new config moved primary to
        # "new-primary".
        active_session_model=lambda: "old-primary",
    )

    result = await reloader.reload()

    assert result.ok is True
    assert result.primary_model_drift == ("old-primary", "new-primary")


@pytest.mark.asyncio
async def test_reload_no_drift_when_primary_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same primary model id — drift field is None."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("HOME", str(home))
    _write_user_config(home)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    loader, docs = _make_loaders(repo)
    watcher = FileWatcher(
        watch_set=build_watch_set([]),
        observers=[],
        poll_interval_s=0.5,
    )
    reloader = Reloader(
        profile_name=None,
        orchestrator=_make_orch_stub(),  # type: ignore[arg-type]
        convention_loader=loader,
        profile_doc_loader=docs,
        file_watcher=watcher,
        project_dir=repo,
        active_session_model=lambda: "test-primary",
    )

    result = await reloader.reload()

    assert result.ok is True
    assert result.primary_model_drift is None


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def test_summarise_zero_entries() -> None:
    assert Reloader._summarise([]) == "no watched files"  # noqa: SLF001


def test_summarise_categories_count() -> None:
    rows = [
        ("config", object()),
        ("config", object()),
        ("convention", object()),
    ]
    out = Reloader._summarise(rows)  # type: ignore[arg-type]  # noqa: SLF001
    assert "2 config layers" in out
    assert "1 convention file" in out
    assert "profile doc" not in out
