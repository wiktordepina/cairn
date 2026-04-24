"""Slash-command registry + dispatcher.

Current catalogue: `/new`, `/ephemeral`, `/quit`, `/cost`,
`/tools`, `/context`, `/help`. Later tranches extend the
catalogue; new commands land in follow-up PRs rather than gating
on a tranche flag (resolved Q10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from cairn.ui._app import CairnApp


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """One slash command.

    `name` includes the leading slash. `handler` is invoked with the
    app and the remainder of the command line (everything after the
    first whitespace).
    """

    name: str
    summary: str
    handler: Callable[[CairnApp, str], Awaitable[None]]


class CommandRegistry:
    """In-memory slash-command catalogue.

    Tranche 1 exposes a flat registry; subagent dispatch (`@name`)
    is V2 and lives outside this registry.
    """

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        if not cmd.name.startswith("/"):
            raise ValueError(f"slash command name must start with '/': {cmd.name!r}")
        if cmd.name in self._commands:
            raise ValueError(f"duplicate slash command: {cmd.name!r}")
        self._commands[cmd.name] = cmd

    def get(self, name: str) -> SlashCommand | None:
        return self._commands.get(name)

    def match(self, prefix: str) -> list[SlashCommand]:
        """Commands whose name starts with *prefix* (for typeahead)."""
        return sorted(
            (cmd for cmd in self._commands.values() if cmd.name.startswith(prefix)),
            key=lambda c: c.name,
        )

    def all(self) -> Sequence[SlashCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    async def dispatch(self, app: CairnApp, line: str) -> DispatchResult:
        """Parse *line* and invoke the matching command.

        Returns a `DispatchResult` so callers can decide how to render
        unknown-command errors (typically an inline chat-log banner).
        """
        stripped = line.strip()
        if not stripped.startswith("/"):
            return DispatchResult(status="not_a_command", message=None)
        parts = stripped.split(maxsplit=1)
        name = parts[0]
        tail = parts[1] if len(parts) > 1 else ""
        cmd = self._commands.get(name)
        if cmd is None:
            return DispatchResult(status="unknown", message=f"unknown command: {name}")
        await cmd.handler(app, tail)
        return DispatchResult(status="ok", message=None)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of a `CommandRegistry.dispatch` call."""

    status: str  # 'ok' | 'unknown' | 'not_a_command'
    message: str | None


# ---------------------------------------------------------------------------
# Tranche 1 handlers
# ---------------------------------------------------------------------------


async def _handle_quit(app: CairnApp, _tail: str) -> None:
    app.exit()


async def _handle_help(app: CairnApp, _tail: str) -> None:
    """Print the current command catalogue as a muted banner."""
    from cairn.ui._widgets import Banner

    registry = app.command_registry
    lines = [f"{cmd.name} — {cmd.summary}" for cmd in registry.all()]
    text = "Commands:\n" + "\n".join(lines)
    screen = app.current_session_screen
    if screen is None:
        return
    screen.append_banner(Banner(text=text, kind="muted"))


async def _handle_cost(app: CairnApp, _tail: str) -> None:
    """Show the current session + daily cost.

    Tranche 1 reads the values from the cost meter (authoritative
    feed from the cost-tracker lands with the bootstrap PR).
    """
    from cairn.ui._widgets import Banner, CostMeter

    screen = app.current_session_screen
    if screen is None:
        return
    cost = screen.current_cost_usd
    precision = screen.query_one(CostMeter).precision
    screen.append_banner(Banner(text=f"session cost: ${cost:.{precision}f}", kind="muted"))


async def _handle_tools(app: CairnApp, _tail: str) -> None:
    """Enumerate the tools available for this session.

    The tool registry is attached to the app at bootstrap time; until
    that lands, display a placeholder so `/tools` still responds.
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    registry = app.tool_registry
    if registry is None:
        screen.append_banner(
            Banner(
                text="/tools — tool registry wiring arrives with the CLI bootstrap",
                kind="muted",
            )
        )
        return
    tools = registry.for_session(screen.session)
    names = sorted(t.name for t in tools)
    text = "tools: " + (", ".join(names) if names else "(none)")
    screen.append_banner(Banner(text=text, kind="muted"))


async def _handle_context(app: CairnApp, _tail: str) -> None:
    """Render the current session's context-budget usage.

    Reads from the screen's bootstrap-supplied `context_source`
    callback; falls back to a muted "unavailable" banner if no
    source is wired (test harnesses with mock orchestrators).
    """
    from cairn.ui._context_report import format_context_report
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    source = screen.context_source
    if source is None:
        screen.append_banner(
            Banner(
                text="/context — bootstrap did not wire a context source",
                kind="muted",
            )
        )
        return
    try:
        report = await source()
    except Exception:  # noqa: BLE001 — /context must never break a session
        log.exception("/context: source raised; rendering fallback")
        screen.append_banner(Banner(text="/context — failed to load (see log)", kind="warning"))
        return
    screen.append_banner(Banner(text=format_context_report(report), kind="muted"))


async def _handle_new(app: CairnApp, _tail: str) -> None:
    """Placeholder for `/new`. Full session-type picker is Tranche 2."""
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is not None:
        screen.append_banner(
            Banner(text="/new — session-type picker lands in Tranche 2", kind="muted")
        )


async def _handle_ephemeral(app: CairnApp, tail: str) -> None:
    """Placeholder for `/ephemeral`. Ephemeral-session spawn is Tranche 2."""
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is not None:
        target = tail.strip() or "(model unspecified)"
        screen.append_banner(
            Banner(
                text=f"/ephemeral {target} — ephemeral session spawn lands in Tranche 2",
                kind="muted",
            )
        )


def build_default_registry() -> CommandRegistry:
    """Return a `CommandRegistry` populated with the Tranche 1 set."""
    registry = CommandRegistry()
    registry.register(SlashCommand(name="/help", summary="list commands", handler=_handle_help))
    registry.register(
        SlashCommand(name="/cost", summary="show current session cost", handler=_handle_cost)
    )
    registry.register(
        SlashCommand(name="/tools", summary="list available tools", handler=_handle_tools)
    )
    registry.register(
        SlashCommand(
            name="/context",
            summary="show context-budget usage for this session",
            handler=_handle_context,
        )
    )
    registry.register(
        SlashCommand(name="/new", summary="new session (Tranche 2)", handler=_handle_new)
    )
    registry.register(
        SlashCommand(
            name="/ephemeral",
            summary="one-shot ephemeral session (Tranche 2)",
            handler=_handle_ephemeral,
        )
    )
    registry.register(SlashCommand(name="/quit", summary="exit the app", handler=_handle_quit))
    return registry
