"""Tests for the bootstrap-time tool stack assembly.

Focused on the warning-on-unknown-override logic added by the
v1-loose-ends brick. The full tool stack (sandbox, registry, runner)
is exercised here against a tmp workspace so we can assert that
unknown override names log a warning without failing the bootstrap.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cairn.config import ToolsConfig
from cairn.persistence import ApprovalDecisionRepo, ToolCallRepo
from cairn.ui._bootstrap import _build_tool_stack

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from cairn.orchestrator import FrozenClock
    from cairn.persistence._connection import Database


class TestBootstrapTimeoutOverrides:
    def test_empty_overrides_log_no_warnings(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="cairn.ui._bootstrap"):
            _build_tool_stack(
                clock=frozen_clock,  # pyright: ignore[reportArgumentType]
                tool_call_repo=ToolCallRepo(db),
                approval_repo=ApprovalDecisionRepo(db),
                workspace_root=tmp_path,
                tools_config=ToolsConfig(),
            )
        assert not [r for r in caplog.records if "timeout_s_overrides" in r.message]

    def test_known_override_logs_no_warning(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="cairn.ui._bootstrap"):
            _build_tool_stack(
                clock=frozen_clock,  # pyright: ignore[reportArgumentType]
                tool_call_repo=ToolCallRepo(db),
                approval_repo=ApprovalDecisionRepo(db),
                workspace_root=tmp_path,
                tools_config=ToolsConfig(timeout_s_overrides={"file_read": 30.0}),
            )
        assert not [r for r in caplog.records if "timeout_s_overrides" in r.message]

    def test_unknown_override_logs_warning_but_does_not_fail(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="cairn.ui._bootstrap"):
            registry, runner, *_ = _build_tool_stack(
                clock=frozen_clock,  # pyright: ignore[reportArgumentType]
                tool_call_repo=ToolCallRepo(db),
                approval_repo=ApprovalDecisionRepo(db),
                workspace_root=tmp_path,
                tools_config=ToolsConfig(timeout_s_overrides={"never_existed": 30.0}),
            )
        warnings = [r for r in caplog.records if "timeout_s_overrides" in r.message]
        assert len(warnings) == 1
        assert "never_existed" in warnings[0].getMessage()
        # Bootstrap continues — the registry and runner are still usable.
        assert registry is not None
        assert runner is not None

    def test_mixed_known_and_unknown_warns_only_for_unknown(
        self,
        db: Database,
        frozen_clock: FrozenClock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="cairn.ui._bootstrap"):
            _build_tool_stack(
                clock=frozen_clock,  # pyright: ignore[reportArgumentType]
                tool_call_repo=ToolCallRepo(db),
                approval_repo=ApprovalDecisionRepo(db),
                workspace_root=tmp_path,
                tools_config=ToolsConfig(
                    timeout_s_overrides={
                        "file_read": 30.0,
                        "ghost_tool": 30.0,
                    }
                ),
            )
        warnings = [r for r in caplog.records if "timeout_s_overrides" in r.message]
        # One warning per unknown override name. file_read is known, so
        # it isn't the *subject* of a warning even though it appears in
        # the "known tools: [...]" tail of the unknown-name warning.
        assert len(warnings) == 1
        # The first %-formatted positional arg is the unknown tool name.
        assert warnings[0].args[0] == "ghost_tool"
