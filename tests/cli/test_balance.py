"""Tests for `cairn balance` — fan-out CLI subcommand.

Each test stubs the per-adapter ``balance()`` method to keep the CLI
test focused on fan-out / formatting / exit-code behaviour rather than
HTTP. The HTTP path itself is covered in ``tests/providers/test_balance.py``.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from cairn.cli import app
from cairn.domain._provider import BalanceInfo
from cairn.providers._anthropic import AnthropicProvider
from cairn.providers._deepseek import DeepSeekProvider
from cairn.providers._openai import OpenAIProvider
from cairn.providers._openrouter import OpenRouterProvider

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


_USER_CONFIG_TOML = textwrap.dedent(
    """\
    schema_version = 1
    active_profile = "personal"

    [providers.anthropic]
    api_key = "env:ANTHROPIC_API_KEY"

    [providers.openai]
    api_key = "env:OPENAI_API_KEY"

    [providers.openrouter]
    api_key = "env:OPENROUTER_API_KEY"

    [providers.deepseek]
    api_key = "env:DEEPSEEK_API_KEY"

    [profiles.personal]
    name = "Cairn"
    soul_document_path = "/tmp/soul.md"
    user_context_path = "/tmp/user.md"
    memory_md_path = "/tmp/MEMORY.md"
    primary_model = "role:primary"
    utility_model = "role:utility"
    """
)


def _write_user_config(home: Path, body: str = _USER_CONFIG_TOML) -> Path:
    user_dir = home / "home" / ".config" / "cairn"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / "config.toml"
    path.write_text(body)
    return path


def _patch_balance(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deepseek: BalanceInfo | None | Exception = None,
    openrouter: BalanceInfo | None | Exception = None,
    anthropic: BalanceInfo | None | Exception = None,
    openai: BalanceInfo | None | Exception = None,
) -> None:
    """Replace each adapter's ``balance()`` with a deterministic stub."""

    def make(value: BalanceInfo | None | Exception):  # noqa: ANN202
        async def stub(self) -> BalanceInfo | None:  # noqa: ANN001
            if isinstance(value, Exception):
                raise value
            return value

        return stub

    monkeypatch.setattr(DeepSeekProvider, "balance", make(deepseek))
    monkeypatch.setattr(OpenRouterProvider, "balance", make(openrouter))
    monkeypatch.setattr(AnthropicProvider, "balance", make(anthropic))
    monkeypatch.setattr(OpenAIProvider, "balance", make(openai))


class TestBalanceTable:
    def test_renders_all_four_providers(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(isolated_home)
        _patch_balance(
            monkeypatch,
            deepseek=BalanceInfo(
                currency="CNY",
                total=110.0,
                used=0.0,
                remaining=110.0,
                granted=10.0,
                source="/user/balance",
            ),
            openrouter=BalanceInfo(
                currency="USD",
                total=5.0,
                used=0.79,
                remaining=4.21,
                source="/api/v1/credits",
            ),
            anthropic=None,
            openai=None,
        )
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 0, result.output
        # Sorted alphabetically by provider name.
        lines = result.output.strip().splitlines()
        assert lines[0].split()[0] == "provider"
        body = "\n".join(lines[1:])
        # Endpoint-having providers show their numbers.
        assert "deepseek" in body
        assert "CNY 110.00" in body
        assert "/user/balance" in body
        assert "openrouter" in body
        assert "USD 4.21" in body
        assert "USD 0.79" in body
        assert "/api/v1/credits" in body
        # Endpoint-less providers print a "no public API" stub row.
        assert "anthropic" in body
        assert "openai" in body
        assert "no public API" in body

    def test_exits_zero_when_only_endpoint_less_providers_configured(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = textwrap.dedent(
            """\
            schema_version = 1
            active_profile = "personal"

            [providers.anthropic]
            api_key = "env:ANTHROPIC_API_KEY"

            [profiles.personal]
            name = "Cairn"
            soul_document_path = "/tmp/soul.md"
            user_context_path = "/tmp/user.md"
            memory_md_path = "/tmp/MEMORY.md"
            primary_model = "role:primary"
            utility_model = "role:utility"
            """
        )
        _write_user_config(isolated_home, body)
        _patch_balance(monkeypatch, anthropic=None)
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 0, result.output
        assert "no public API" in result.output

    def test_partial_failure_exit_zero(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One provider erroring is fine — exit 0, error inline."""
        _write_user_config(isolated_home)
        _patch_balance(
            monkeypatch,
            deepseek=RuntimeError("boom"),
            openrouter=BalanceInfo(
                currency="USD",
                total=5.0,
                used=1.0,
                remaining=4.0,
                source="/api/v1/credits",
            ),
        )
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 0, result.output
        assert "error: RuntimeError" in result.output
        assert "USD 4.00" in result.output

    def test_total_failure_exit_two(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every endpoint-having provider failing → exit 2."""
        body = textwrap.dedent(
            """\
            schema_version = 1
            active_profile = "personal"

            [providers.deepseek]
            api_key = "env:DEEPSEEK_API_KEY"

            [providers.openrouter]
            api_key = "env:OPENROUTER_API_KEY"

            [profiles.personal]
            name = "Cairn"
            soul_document_path = "/tmp/soul.md"
            user_context_path = "/tmp/user.md"
            memory_md_path = "/tmp/MEMORY.md"
            primary_model = "role:primary"
            utility_model = "role:utility"
            """
        )
        _write_user_config(isolated_home, body)
        _patch_balance(
            monkeypatch,
            deepseek=ConnectionError("offline"),
            openrouter=ConnectionError("offline"),
        )
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 2, result.output

    def test_no_providers_configured_message(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = textwrap.dedent(
            """\
            schema_version = 1
            active_profile = "personal"

            [profiles.personal]
            name = "Cairn"
            soul_document_path = "/tmp/soul.md"
            user_context_path = "/tmp/user.md"
            memory_md_path = "/tmp/MEMORY.md"
            primary_model = "role:primary"
            utility_model = "role:utility"
            """
        )
        _write_user_config(isolated_home, body)
        _patch_balance(monkeypatch)
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 0, result.output
        assert "No providers configured" in result.output

    def test_exit_one_on_invalid_config(
        self, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No config file at all → ConfigError → exit 1.
        result = CliRunner().invoke(app, ["balance"])
        assert result.exit_code == 1, result.output
