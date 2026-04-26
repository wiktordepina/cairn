"""Slash-command registry + dispatcher.

Current catalogue: `/new`, `/ephemeral`, `/quit`, `/cost`,
`/tools`, `/context`, `/help`, `/persona`, `/profile`, `/model`,
`/conventions`. Later tranches extend the catalogue; new commands
land in follow-up PRs rather than gating on a tranche flag
(resolved Q10).
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


async def _handle_persona(app: CairnApp, _tail: str) -> None:
    """Show the active session's persona name."""
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    persona = screen.session.persona
    screen.append_banner(Banner(text=f"persona: {persona}", kind="muted"))


async def _handle_profile(app: CairnApp, _tail: str) -> None:
    """Show the active profile's name and key fields.

    Falls back to a muted banner when the bootstrap hasn't wired a
    `ProfileConfig` (e.g. Pilot tests with mock orchestrators).
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    profile = app.profile
    if profile is None:
        screen.append_banner(
            Banner(
                text="/profile — bootstrap did not wire a profile",
                kind="muted",
            )
        )
        return
    name = profile.name or "(unnamed)"
    lines = [
        f"profile: {name}",
        f"  memory space: {profile.memory_space}",
        f"  primary model: {profile.primary_model}",
        f"  utility model: {profile.utility_model}",
    ]
    screen.append_banner(Banner(text="\n".join(lines), kind="muted"))


async def _handle_model(app: CairnApp, _tail: str) -> None:
    """Open a picker over every configured model.

    The currently-active session model is highlighted; selecting it is
    a no-op. Picking a different model on a fresh session swaps it
    immediately. On an in-progress session (any persisted messages),
    a follow-up modal asks whether to keep history (same row, swap
    model), start fresh (archive + new session), or abort.
    """
    from cairn.ui._screens import Choice, ChoiceModal, ModelPickerModal
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    registry = app.model_registry
    if registry is None:
        screen.append_banner(
            Banner(
                text=f"/model — registry unwired (session model: {screen.session.model})",
                kind="muted",
            )
        )
        return

    models = registry.models()
    if not models:
        screen.append_banner(Banner(text="/model — no models configured", kind="muted"))
        return

    if screen.is_turn_active():
        screen.append_banner(
            Banner(
                text="/model — busy; finish the current turn first",
                kind="muted",
            )
        )
        return

    picked: str | None = await app.push_screen_wait(
        ModelPickerModal(
            models=models,
            current_model_id=screen.session.model,
            profile=app.profile,
        )
    )
    if picked is None:
        return
    if picked == screen.session.model:
        screen.append_banner(Banner(text="model unchanged", kind="muted"))
        return

    # Fresh session (no messages yet) → swap immediately.
    msg_count = await app.orchestrator.count_session_messages(screen.session.id)
    if msg_count == 0:
        refreshed = await app.orchestrator.swap_session_model(
            screen.session.id, picked, mode="keep"
        )
        screen.replace_session(refreshed)
        screen.append_banner(Banner(text=f"model → {picked}", kind="muted"))
        return

    # In-progress: ask the user how to handle history.
    choice = await app.push_screen_wait(
        ChoiceModal(
            title=f"Switch model: {screen.session.model} → {picked}",
            body=(
                f"This session has {msg_count} message(s). The new model will "
                "re-read the existing transcript on its next turn (prompt cache "
                "will invalidate; the auto-compactor will trim aggressively if "
                "the new model has a smaller context window)."
            ),
            choices=(
                Choice(key="k", label="Keep history", variant="primary"),
                Choice(key="s", label="Start fresh", variant="warning"),
                Choice(key="a", label="Abort", variant="default"),
            ),
            default_key="k",
        )
    )
    if choice in (None, "a"):
        screen.append_banner(Banner(text="model swap aborted", kind="muted"))
        return
    if choice == "k":
        refreshed = await app.orchestrator.swap_session_model(
            screen.session.id, picked, mode="keep"
        )
        screen.replace_session(refreshed)
        screen.append_banner(Banner(text=f"model → {picked}  (history kept)", kind="muted"))
        return
    if choice == "s":
        old_id = screen.session.id
        await app.orchestrator.archive_session(old_id)
        new_session = await app.orchestrator.start_session(
            type=screen.session.type,
            persona=screen.session.persona,
            model=picked,
        )
        screen.replace_session(new_session)
        screen.clear_transcript()
        screen.append_banner(
            Banner(
                text=f"model → {picked}  (fresh session)",
                kind="muted",
            )
        )
        return


async def _handle_reload(app: CairnApp, _tail: str) -> None:
    """Re-load config + conventions + profile docs.

    Surgical reload: rebuilds the disk-derived collaborators on the
    running orchestrator (provider registry, model registry, secret
    resolver), invalidates the convention and profile-doc loader
    caches, and re-snapshots the file watcher's baseline. The active
    session, persistence, and the extraction worker are preserved.

    Mid-turn invocations queue: the reload runs after the current
    turn completes so the in-flight provider call finishes against
    the bound config.
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    reloader = app.reloader
    if reloader is None:
        screen.append_banner(
            Banner(
                text="/reload — bootstrap did not wire a file watcher",
                kind="muted",
            ),
        )
        return

    if screen.is_turn_active():
        screen.queue_reload()
        screen.append_banner(
            Banner(
                text="reload queued — applies after current turn",
                kind="muted",
            ),
        )
        return

    result = await reloader.reload()
    kind = "muted" if result.ok else "warning"
    text = result.summary if result.ok else f"reload failed — {result.error}"
    screen.append_banner(Banner(text=text, kind=kind))
    if result.ok and result.primary_model_drift is not None:
        active, new = result.primary_model_drift
        refreshed = await app.orchestrator.swap_session_model(
            screen.session.id, new, mode="revert"
        )
        screen.replace_session(refreshed)
        screen.append_banner(
            Banner(
                text=(f"session model reverted to config — was {active}, now {new}"),
                kind="muted",
            ),
        )


async def _handle_conventions(app: CairnApp, _tail: str) -> None:
    """List discovered convention files and the project's trust state.

    Discovery is run directly (no trust prompt, no I/O of file
    contents) so the user can see what *would* be loaded even when
    the project isn't trusted. Project-level files inherit the
    project root's trust state from the user-level allowlist;
    user-level files are always trusted.
    """
    from cairn.conventions import discover_project_files, discover_user_files, find_git_root
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    profile = app.profile
    loader = app.convention_loader
    allowlist = app.allowlist_store
    if profile is None or loader is None or allowlist is None:
        screen.append_banner(
            Banner(
                text="/conventions — bootstrap did not wire convention sources",
                kind="muted",
            )
        )
        return
    config = profile.convention_files
    if not config.enabled:
        screen.append_banner(
            Banner(text="/conventions — disabled in profile config", kind="muted")
        )
        return

    cwd = loader.cwd
    project_files = discover_project_files(cwd, config)
    user_files = discover_user_files(config.user_level_paths)
    project_root = find_git_root(cwd) or cwd
    project_trusted = allowlist.contains(project_root)

    lines: list[str] = []
    if project_files:
        trust_label = "trusted" if project_trusted else "untrusted"
        lines.append(f"project: {project_root}  ({trust_label})")
        for path, filename, _source in project_files:
            lines.append(f"  {filename}  {path}")
    else:
        lines.append(f"project: {project_root}  (no convention files)")
    if user_files:
        lines.append("user:")
        for path, filename, _source in user_files:
            lines.append(f"  {filename}  {path}")
    screen.append_banner(Banner(text="\n".join(lines), kind="muted"))


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
    registry.register(
        SlashCommand(name="/persona", summary="show active persona", handler=_handle_persona)
    )
    registry.register(
        SlashCommand(
            name="/profile",
            summary="show active profile and key fields",
            handler=_handle_profile,
        )
    )
    registry.register(
        SlashCommand(
            name="/model",
            summary="show resolved models for this session",
            handler=_handle_model,
        )
    )
    registry.register(
        SlashCommand(
            name="/conventions",
            summary="list discovered convention files and trust state",
            handler=_handle_conventions,
        )
    )
    registry.register(
        SlashCommand(
            name="/reload",
            summary="reload config + conventions + profile docs",
            handler=_handle_reload,
        )
    )
    return registry
