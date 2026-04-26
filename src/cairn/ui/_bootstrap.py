"""Orchestrator + Textual app assembly.

Wires every collaborator needed to drive a real session:

- config resolution (profile selection, secret resolver, model registry)
- persistence (database, repositories)
- providers
- memory services + extraction queue
- tool system (registry, runner, approvers, transformers)
- orchestrator with the above
- Textual app + observer + approval gateway

Everything stays on a single asyncio event loop: the sync collaborator
graph is built first, then `App.run_async()` drives the UI on the same
loop that owns the `Database` connection and the extraction worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cairn.compaction import TruncatingCompactor
from cairn.config import ConfigError, ModelRegistry, SecretResolver, ToolsConfig, load_config
from cairn.conventions import (
    AllowlistStore,
    ConventionLoader,
    default_allowlist_path,
    trust_gate_for_policy,
)
from cairn.domain._enums import SessionType
from cairn.logging import StructuredEventObserver, make_event_logger
from cairn.memory import (
    Extractor,
    ObservationExtractionQueue,
    ObservationLog,
    ProfileDocLoader,
    StandardContextManager,
)
from cairn.memory._retrieval import MemoryService
from cairn.orchestrator import (
    BasicCostTracker,
    Orchestrator,
    OrchestratorConfig,
    SessionManager,
    SystemClock,
)
from cairn.persistence import (
    ApprovalDecisionRepo,
    Database,
    MemoryRepo,
    MessageRepo,
    SessionRepo,
    ToolCallRepo,
    TurnRepo,
    UsageRepo,
    db_path_for_config,
    memory_dir_for_profile,
    prompt_history_path,
)
from cairn.providers import ProviderRegistry
from cairn.tools import (
    AutoApproveReadOnly,
    DefaultToolRegistry,
    DefaultToolRunner,
    InvisibleUnicodeStripper,
    SecretRedactor,
    SessionAllowlist,
    SpotlightTransformer,
    TierGate,
)
from cairn.tools.builtin import (
    make_file_read,
    make_file_write,
    make_grep,
    make_now,
    make_web_fetch,
)
from cairn.tools.security import WorkspaceSandbox
from cairn.ui._app import CairnApp
from cairn.ui._context_report import ContextReportInput
from cairn.ui._gateway import TextualApprovalGateway
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._prompt_history import PromptHistoryStore
from cairn.ui._trust_gate import TextualPromptTrustGate
from cairn.watcher import (
    FileWatcher,
    Reloader,
    build_watch_set,
    discover_watch_paths,
)

if TYPE_CHECKING:
    from cairn.config._models import CairnConfig
    from cairn.domain import UIEvent
    from cairn.orchestrator._middleware import ResultTransformer, ToolApprover
    from cairn.orchestrator._protocols import ApprovalGateway


log = logging.getLogger(__name__)


def launch(*, profile_name: str | None) -> int:
    """Assemble the orchestrator + Textual app and run it.

    Returns a process exit code.
    """
    try:
        config = load_config(profile=profile_name)
    except ConfigError as exc:
        sys.stderr.write(f"cairn: config error: {exc}\n")
        return 2

    try:
        return asyncio.run(_run(config, profile_name=profile_name))
    except KeyboardInterrupt:
        return 130


async def _run(config: CairnConfig, *, profile_name: str | None = None) -> int:
    active = config.active
    profile_key = active.name or config.active_profile

    # -- Sync collaborator graph ----------------------------------------
    clock = SystemClock()
    secret_resolver = SecretResolver()
    model_registry = ModelRegistry(config.models)
    provider_registry = ProviderRegistry(config=config, secret_resolver=secret_resolver)

    database = Database(db_path_for_config(config))
    session_repo = SessionRepo(database)
    message_repo = MessageRepo(database)
    turn_repo = TurnRepo(database)
    tool_call_repo = ToolCallRepo(database)
    approval_repo = ApprovalDecisionRepo(database)
    memory_repo = MemoryRepo(database)
    usage_repo = UsageRepo(database)

    cost_tracker = BasicCostTracker(
        usage_repo=usage_repo,
        clock=clock,
        budgets=active.budgets,
    )
    session_manager = SessionManager(
        session_repo=session_repo,
        model_registry=model_registry,
        clock=clock,
        default_model_ref=active.primary_model,
    )

    doc_loader = ProfileDocLoader(
        soul_document_path=active.soul_document_path,
        user_context_path=active.user_context_path,
        memory_md_path=active.memory_md_path,
    )
    # Convention loader is constructed with a placeholder trust gate
    # when the policy is "prompt"; the real gate (`TextualPromptTrustGate`)
    # needs the app, which is constructed later. The swap happens
    # post-app-construction below — same circular-dep pattern the
    # approval gateway uses.
    allowlist_store = AllowlistStore(default_allowlist_path())
    conventions_policy = active.convention_files.trust_policy
    initial_trust_gate = trust_gate_for_policy(
        conventions_policy,
        allowlist_store=allowlist_store,
    )
    convention_loader = ConventionLoader(
        config=active.convention_files,
        trust_gate=initial_trust_gate,
        cwd=Path.cwd(),
    )
    context_manager = StandardContextManager(
        loader=doc_loader,
        conventions=convention_loader,
        clock=clock,
        timezone_name=active.locale.timezone,
    )

    memory_service = MemoryService(
        memory_repo=memory_repo,
        clock=clock,
        memory_config=active.memory,
    )
    observation_log = ObservationLog(root=memory_dir_for_profile(profile_key))
    extractor = Extractor(
        memory_repo=memory_repo,
        observation_log=observation_log,
        provider_registry=provider_registry,
        model_registry=model_registry,
        cost_tracker=cost_tracker,
        clock=clock,
        memory_config=active.memory,
    )
    structured_observer = StructuredEventObserver(make_event_logger(profile_key))
    extraction_queue = ObservationExtractionQueue(
        extractor=extractor,
        session_repo=session_repo,
        message_repo=message_repo,
        memory_config=active.memory,
        observers=(structured_observer,),
    )

    tool_registry, tool_runner, session_allowlist, approvers, transformers = _build_tool_stack(
        clock=clock,
        tool_call_repo=tool_call_repo,
        approval_repo=approval_repo,
        workspace_root=Path.cwd(),
        tools_config=active.tools,
        timezone_name=active.locale.timezone,
    )

    # Auto-compactor: trims the outgoing provider request when history
    # plus tools plus system prompt approach the model's context
    # window. Routed through the structured observer so
    # ``HistoryCompacted`` and ``BudgetOverflowAdvisory`` land in
    # ``cairn.events``. Pre-0.18.0 the orchestrator was constructed
    # without this preparer, leaving auto-compaction inert.
    async def _compactor_event_sink(event: UIEvent) -> None:
        structured_observer.observe(event)

    compactor = TruncatingCompactor(
        config=active.compaction,
        model_registry=model_registry,
        provider_registry=provider_registry,
        event_sink=_compactor_event_sink,
    )

    orchestrator = Orchestrator(
        provider_registry=provider_registry,
        model_registry=model_registry,
        session_manager=session_manager,
        context_manager=context_manager,
        tool_registry=tool_registry,
        tool_runner=tool_runner,
        memory_service=memory_service,
        extraction_queue=extraction_queue,
        approval_gateway=cast("ApprovalGateway", _PendingGateway()),
        cost_tracker=cost_tracker,
        turn_repo=turn_repo,
        message_repo=message_repo,
        clock=clock,
        config=OrchestratorConfig(),
        preparers=(compactor,),
        approvers=approvers,
        transformers=transformers,
        observers=(),
    )

    # -- Async setup that needs the loop -------------------------------
    # Sweep for turns the previous process left non-terminal; the UI
    # surfaces a banner when the count > 0.
    resumed = await orchestrator.resume_aborted_turns()
    session = await orchestrator.start_session(
        type=SessionType.COMPANION,
        persona="companion",
    )
    await extraction_queue.start()

    async def _session_cost() -> float:
        return await usage_repo.total_cost_for_session(session.id)

    primary_model = model_registry.resolve(active.primary_model)

    async def _context_report() -> ContextReportInput:
        return ContextReportInput(
            context_window=primary_model.context_window,
            model=primary_model.id,
            last_usage=await usage_repo.most_recent_primary_turn(session.id),
        )

    prompt_history_store = PromptHistoryStore(
        path=prompt_history_path(profile_key, Path.cwd()),
        max_size=active.ui.prompt_history_size,
    )

    # File watcher + /reload Reloader. Both are optional — disabled
    # profiles get None on the app, and the /reload handler renders a
    # placeholder banner.
    file_watcher: FileWatcher | None = None
    reloader: Reloader | None = None
    if active.watcher.enabled:
        watch_rows = discover_watch_paths(
            convention_files_config=active.convention_files,
            convention_loader=convention_loader,
            profile_doc_loader=doc_loader,
        )
        watch_set = build_watch_set(watch_rows)
        # The watcher's `is_turn_active` getter consults the active
        # session screen; it's lazily-bound below since the app doesn't
        # exist yet at this point.
        file_watcher = FileWatcher(
            watch_set=watch_set,
            observers=(structured_observer,),
            poll_interval_s=active.watcher.poll_interval_s,
        )
        # V1 is single-session — capture its model id so the Reloader
        # can detect role-pin drift against the active session.
        reloader = Reloader(
            profile_name=profile_name,
            orchestrator=orchestrator,
            convention_loader=convention_loader,
            profile_doc_loader=doc_loader,
            file_watcher=file_watcher,
            active_session_model=lambda: session.model,
        )

    app = CairnApp(
        orchestrator=orchestrator,
        session=session,
        tool_registry=tool_registry,
        ui_config=active.ui,
        cost_source=_session_cost,
        context_source=_context_report,
        resumed_turn_count=len(resumed),
        profile=active,
        model_registry=model_registry,
        convention_loader=convention_loader,
        allowlist_store=allowlist_store,
        prompt_history_store=prompt_history_store,
        reloader=reloader,
    )
    orchestrator._approval_gateway = TextualApprovalGateway(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        app=app,
        session_allowlist=session_allowlist,
    )
    textual_observer = TextualUIEventObserver(app=app)
    orchestrator._observers = (  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        textual_observer,
        structured_observer,
    )
    if file_watcher is not None:
        # Drift events also need to reach the UI so the user sees a
        # banner — the watcher was constructed before the textual
        # observer existed, so attach it here.
        file_watcher.add_observer(textual_observer)

    if conventions_policy == "prompt":
        convention_loader._trust_gate = TextualPromptTrustGate(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            app=app,
            store=allowlist_store,
        )

    if file_watcher is not None:
        # Bind the watcher's turn-active gate to the live session
        # screen. Drift detected during a turn is buffered until the
        # next tick after the turn completes.
        def _is_turn_active() -> bool:
            screen = app.current_session_screen
            return screen is not None and screen.is_turn_active()

        file_watcher._is_turn_active = _is_turn_active  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        await file_watcher.start()

    # Textual's `log` falls back to bare `print()` when the active-app
    # context var is unset (see `textual/__init__.py:74-90`). During
    # the post-exit teardown — widget Prune/Unmount messages dispatch
    # after `App.run_async()` has cleared `active_app` — those prints
    # leak to stdout *after* the alt-screen has already been
    # restored, leaving "Prune() >>> Banner() method=..." debris on
    # the user's terminal. The driver writes to `sys.__stdout__`
    # directly, so redirecting `sys.stdout` for the run window
    # silences the leak without affecting the rendered UI.
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            await app.run_async()
    finally:
        if file_watcher is not None:
            await file_watcher.stop()
        await extraction_queue.stop()
        await database.close()

    return 0


def _build_tool_stack(
    *,
    clock: SystemClock,
    tool_call_repo: ToolCallRepo,
    approval_repo: ApprovalDecisionRepo,
    workspace_root: Path,
    tools_config: ToolsConfig,
    timezone_name: str | None = None,
) -> tuple[
    DefaultToolRegistry,
    DefaultToolRunner,
    SessionAllowlist,
    tuple[ToolApprover, ...],
    tuple[ResultTransformer, ...],
]:
    sandbox = WorkspaceSandbox(root=workspace_root)
    companion_tools = [
        make_file_read(sandbox),
        make_file_write(sandbox),
        make_grep(sandbox),
        make_now(clock, timezone_name=timezone_name),
        make_web_fetch(),
    ]
    tool_registry = DefaultToolRegistry(companion_tools=companion_tools)
    overrides = tools_config.timeout_s_overrides
    if overrides:
        known = tool_registry.names()
        for name in sorted(overrides.keys() - known):
            log.warning(
                "tools.timeout_s_overrides references unknown tool %r; ignoring (known tools: %s)",
                name,
                sorted(known),
            )
    tool_runner = DefaultToolRunner(
        tool_call_repo=tool_call_repo,
        approval_repo=approval_repo,
        clock=clock,
        timeout_overrides=overrides,
    )
    session_allowlist = SessionAllowlist()
    approvers: tuple[ToolApprover, ...] = (
        AutoApproveReadOnly(),
        session_allowlist,
        TierGate(),
    )
    transformers: tuple[ResultTransformer, ...] = (
        SpotlightTransformer(),
        SecretRedactor(),
        InvisibleUnicodeStripper(),
    )
    return tool_registry, tool_runner, session_allowlist, approvers, transformers


class _PendingGateway:
    """Sentinel gateway used only during orchestrator construction.

    Swapped for a `TextualApprovalGateway` as soon as the app exists
    (the gateway needs an app reference; the app needs an orchestrator
    reference — the circle resolves by assigning the real gateway
    post-construction).
    """

    async def request(
        self,
        *,
        session_id: str,  # noqa: ARG002
        tool_call_id: str,  # noqa: ARG002
        request: object,  # noqa: ARG002
    ) -> object:
        raise RuntimeError(
            "approval gateway was queried before the UI was wired; this is a bootstrap bug"
        )
