"""Slash-command registry + dispatcher.

Current catalogue: `/help`, `/cost`, `/tools`, `/context`,
`/clear`, `/archive`, `/ephemeral`, `/quit`, `/persona`,
`/profile`, `/model`, `/conventions`, `/reload`, `/recall`,
`/remember`. New commands land in follow-up PRs rather than
gating on a tranche flag (resolved Q10).
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
    """Render the multi-window cost report.

    Falls back to the cost-meter feed when the bootstrap hasn't
    plumbed the usage repo through (test harnesses with mock
    orchestrators).
    """
    from cairn.ui._cost_report import render_cost_summary
    from cairn.ui._widgets import Banner, CostMeter

    screen = app.current_session_screen
    if screen is None:
        return

    usage_repo = app.usage_repo
    clock = app.clock
    profile_key = app.active_profile_key
    if usage_repo is None or clock is None or profile_key is None:
        cost = screen.current_cost_usd
        precision = screen.query_one(CostMeter).precision
        screen.append_banner(Banner(text=f"session cost: ${cost:.{precision}f}", kind="muted"))
        return

    tz = _resolve_locale_timezone(app.profile.locale.timezone if app.profile else None)
    summary = await usage_repo.cost_summary(
        session_id=screen.session.id,
        profile=profile_key,
        now=clock.now(),
        timezone=tz,
    )
    screen.append_banner(Banner(text=render_cost_summary(summary), kind="muted"))


def _resolve_locale_timezone(name: str | None):
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: PLC0415

    if name is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            log.warning("locale.timezone %r not found; falling back to system tz", name)
    local = datetime.now().astimezone().tzinfo
    assert local is not None
    return local


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


async def _handle_clear(app: CairnApp, _tail: str) -> None:
    """Archive the current session and open a fresh one in its place.

    The new session inherits the current type and persona; its model
    re-resolves from config (any runtime ``/model`` swap is dropped,
    consistent with the ``/reload`` revert rule). Mid-turn invocations
    are refused — finish the turn first.
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    if screen.is_turn_active():
        screen.append_banner(
            Banner(text="/clear — busy; finish the current turn first", kind="muted")
        )
        return
    current = screen.session
    await app.orchestrator.archive_session(current.id)
    new_session = await app.orchestrator.start_session(
        type=current.type,
        persona=current.persona,
    )
    screen.replace_session(new_session)
    screen.clear_transcript()
    screen.append_banner(Banner(text="session cleared", kind="muted"))


async def _handle_archive(app: CairnApp, _tail: str) -> None:
    """Archive the current session and exit.

    V1 has no session picker, so post-archive there is nowhere to go.
    The user is informed up front via a confirm modal — accidentally
    triggered ``/archive`` doesn't kill the app silently.
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    if screen.is_turn_active():
        screen.append_banner(
            Banner(text="/archive — busy; finish the current turn first", kind="muted")
        )
        return

    # `push_screen_wait` requires a worker context — see `_handle_model`.
    app.run_worker(_run_archive(app), name="archive", exit_on_error=False)


async def _run_archive(app: CairnApp) -> None:
    from cairn.ui._screens import Choice, ChoiceModal
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    choice = await app.push_screen_wait(
        ChoiceModal(
            title="Archive this session?",
            body=(
                "Archiving will close the app — V1 has no session picker yet, "
                "so there is nowhere to go after the archive."
            ),
            choices=(
                Choice(key="a", label="Archive and quit", variant="warning"),
                Choice(key="c", label="Cancel"),
            ),
            default_key="c",
        )
    )
    if choice in (None, "c"):
        screen.append_banner(Banner(text="archive cancelled", kind="muted"))
        return
    await app.orchestrator.archive_session(screen.session.id)
    app.exit()


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
        f"  primary model: {_format_role_ref(app, profile.primary_model)}",
        f"  utility model: {_format_role_ref(app, profile.utility_model)}",
    ]
    screen.append_banner(Banner(text="\n".join(lines), kind="muted"))


def _format_role_ref(app: CairnApp, ref: str) -> str:
    """Render a profile model ref (id or ``role:<name>``) with its display name.

    Falls back to the raw ref when the registry is unwired or the ref
    can't be resolved (test harnesses, or a config that drifted away
    from what the running session was started with).
    """
    registry = app.model_registry
    if registry is None:
        return ref
    from cairn.config._registry import ModelNotFoundError

    try:
        model = registry.resolve(ref)
    except ModelNotFoundError:
        return ref
    return f"{model.display_name}  ({ref})"


async def _handle_model(app: CairnApp, _tail: str) -> None:
    """Open a picker over every configured model.

    The currently-active session model is highlighted; selecting it is
    a no-op. Picking a different model on a fresh session swaps it
    immediately. On an in-progress session (any persisted messages),
    a follow-up modal asks whether to keep history (same row, swap
    model), start fresh (archive + new session), or abort.
    """
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

    if not registry.models():
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

    # `push_screen_wait` requires a worker context (Textual rule); the
    # slash-handler dispatch path runs on the main loop, so spin a
    # dedicated worker for the modal flow.
    app.run_worker(_run_model_swap(app), name="model-swap", exit_on_error=False)


async def _run_model_swap(app: CairnApp) -> None:
    from cairn.ui._model_label import resolve_label
    from cairn.ui._screens import Choice, ChoiceModal, ModelPickerModal
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    registry = app.model_registry
    if registry is None:
        return

    picked: str | None = await app.push_screen_wait(
        ModelPickerModal(
            models=registry.models(),
            current_model_id=screen.session.model,
            profile=app.profile,
        )
    )
    if picked is None:
        return
    if picked == screen.session.model:
        screen.append_banner(Banner(text="model unchanged", kind="muted"))
        return

    picked_label = resolve_label(registry, picked)
    current_label = resolve_label(registry, screen.session.model)

    msg_count = await app.orchestrator.count_session_messages(screen.session.id)
    if msg_count == 0:
        refreshed = await app.orchestrator.swap_session_model(
            screen.session.id, picked, mode="keep"
        )
        screen.replace_session(refreshed)
        screen.append_banner(Banner(text=f"model → {picked_label}", kind="muted"))
        return

    choice = await app.push_screen_wait(
        ChoiceModal(
            title=f"Switch model: {current_label} → {picked_label}",
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
        screen.append_banner(Banner(text=f"model → {picked_label}  (history kept)", kind="muted"))
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
                text=f"model → {picked_label}  (fresh session)",
                kind="muted",
            )
        )


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
        from cairn.ui._model_label import resolve_label

        active, new = result.primary_model_drift
        refreshed = await app.orchestrator.swap_session_model(
            screen.session.id, new, mode="revert"
        )
        screen.replace_session(refreshed)
        registry = app.model_registry
        was_label = resolve_label(registry, active)
        now_label = resolve_label(registry, new)
        screen.append_banner(
            Banner(
                text=(f"session model reverted to config — was {was_label}, now {now_label}"),
                kind="muted",
            ),
        )


async def _handle_recall(app: CairnApp, tail: str) -> None:
    """Search the active session's memory and render hits in a modal.

    Read-only — nothing about the recall is appended to the
    conversation. The model only sees the result if it issues its
    own ``recall`` tool call.
    """
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    query = tail.strip()
    if not query:
        screen.append_banner(Banner(text="usage: /recall <query>", kind="muted"))
        return
    service = app.memory_service
    if service is None:
        screen.append_banner(Banner(text="/recall — bootstrap did not wire memory", kind="muted"))
        return
    space = screen.session.memory_space
    if not space:
        screen.append_banner(
            Banner(text="/recall — this session has no memory_space", kind="muted")
        )
        return

    app.run_worker(_run_recall(app, query=query, space=space), name="recall", exit_on_error=False)


async def _run_recall(app: CairnApp, *, query: str, space: str) -> None:
    from cairn.ui._screens import MemoryRecallModal

    service = app.memory_service
    screen = app.current_session_screen
    if service is None or screen is None:
        return
    hits = await service.retrieve_scored(space=space, query=query, k=10)
    await app.push_screen_wait(MemoryRecallModal(query=query, hits=hits))


async def _handle_remember(app: CairnApp, tail: str) -> None:
    """Save a fact memory for the active session, bypassing the
    post-turn observation queue.

    Confirmed via toast. Dedup-on-store is unchanged: if the new
    text is near-identical to an existing entry, the existing row's
    `updated_at` is refreshed and the toast names the dedup hit.
    """
    from cairn.domain import MemoryClass, MemoryEntryType
    from cairn.ui._widgets import Banner

    screen = app.current_session_screen
    if screen is None:
        return
    text = tail.strip()
    if not text:
        screen.append_banner(Banner(text="usage: /remember <text>", kind="muted"))
        return
    repo = app.memory_repo
    if repo is None:
        screen.append_banner(
            Banner(text="/remember — bootstrap did not wire memory", kind="muted")
        )
        return
    space = screen.session.memory_space
    if not space:
        screen.append_banner(
            Banner(text="/remember — this session has no memory_space", kind="muted")
        )
        return

    importance = 7
    if app.profile is not None:
        importance = app.profile.memory.explicit_remember_importance

    entry = await repo.store(
        memory_space=space,
        content=text,
        entry_type=MemoryEntryType.FACT,
        memory_class=MemoryClass.SEMANTIC,
        importance=importance,
        source_session_id=screen.session.id,
    )
    # Dedup heuristic: a fresh insert has created_at == updated_at
    # (both set to the same ``now`` inside ``store``); a dedup hit
    # leaves the original ``created_at`` and only refreshes
    # ``updated_at``.
    was_dedup = entry.created_at != entry.updated_at
    if was_dedup:
        app.notify(f"already remembered (entry #{entry.id})", severity="information")
    else:
        app.notify("remembered", severity="information")


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
        SlashCommand(
            name="/clear",
            summary="archive current session and start fresh",
            handler=_handle_clear,
        )
    )
    registry.register(
        SlashCommand(
            name="/archive",
            summary="archive current session and quit",
            handler=_handle_archive,
        )
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
            summary="pick a configured model for this session",
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
    registry.register(
        SlashCommand(
            name="/recall",
            summary="search session memory (top-k FTS5)",
            handler=_handle_recall,
        )
    )
    registry.register(
        SlashCommand(
            name="/remember",
            summary="save a fact to session memory",
            handler=_handle_remember,
        )
    )
    return registry
