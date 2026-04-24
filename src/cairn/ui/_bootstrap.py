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
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cairn.config import ConfigError, ModelRegistry, SecretResolver, load_config
from cairn.domain._enums import SessionType
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
    make_web_fetch,
)
from cairn.tools.security import WorkspaceSandbox
from cairn.ui._app import CairnApp
from cairn.ui._gateway import TextualApprovalGateway
from cairn.ui._observer import TextualUIEventObserver

if TYPE_CHECKING:
    from cairn.config._models import CairnConfig
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
        return asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 130


async def _run(config: CairnConfig) -> int:
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
    context_manager = StandardContextManager(loader=doc_loader, conventions=None)

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
    extraction_queue = ObservationExtractionQueue(
        extractor=extractor,
        session_repo=session_repo,
        message_repo=message_repo,
        memory_config=active.memory,
    )

    tool_registry, tool_runner, session_allowlist, approvers, transformers = _build_tool_stack(
        clock=clock,
        tool_call_repo=tool_call_repo,
        approval_repo=approval_repo,
        workspace_root=Path.cwd(),
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
        approvers=approvers,
        transformers=transformers,
        observers=(),
    )

    # -- Async setup that needs the loop -------------------------------
    session = await orchestrator.start_session(
        type=SessionType.COMPANION,
        persona="companion",
    )
    await extraction_queue.start()

    app = CairnApp(
        orchestrator=orchestrator,
        session=session,
        tool_registry=tool_registry,
    )
    orchestrator._approval_gateway = TextualApprovalGateway(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        app=app,
        session_allowlist=session_allowlist,
    )
    orchestrator._observers = (TextualUIEventObserver(app=app),)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    try:
        await app.run_async()
    finally:
        await extraction_queue.stop()
        await database.close()

    return 0


def _build_tool_stack(
    *,
    clock: SystemClock,
    tool_call_repo: ToolCallRepo,
    approval_repo: ApprovalDecisionRepo,
    workspace_root: Path,
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
        make_web_fetch(),
    ]
    tool_registry = DefaultToolRegistry(companion_tools=companion_tools)
    tool_runner = DefaultToolRunner(
        tool_call_repo=tool_call_repo,
        approval_repo=approval_repo,
        clock=clock,
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
            "approval gateway was queried before the UI was wired; "
            "this is a bootstrap bug"
        )
