"""The Orchestrator — cairn's turn loop.

Drives a state machine per turn, coordinates provider streaming with
middleware chains, persists state at every transition, fans out UI
events to registered observers and to the caller's async-iterator
consumer.

Design doc: `.plan/orchestrator-design.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

from cairn.compaction._errors import BudgetOverflowDeclined
from cairn.domain._content import ToolResultBlock, ToolUseBlock
from cairn.domain._enums import (
    ErrorClass,
    SessionType,
    StopReason,
    ToolCallStatus,
    UsageOperation,
)
from cairn.domain._events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    BudgetWarning,
    DelegationCompleted,
    DelegationSpawned,
    ObservationExtractionRequested,
    SessionArchived,
    SessionCreated,
    SessionResumed,
    ToolCallApproved,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallRejected,
    ToolCallStarted,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
    TurnIncomplete,
    UserMessagePersisted,
)
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.orchestrator._context import TurnContext
from cairn.orchestrator._enums import ApprovalOutcome, BudgetVerdict, TurnState
from cairn.orchestrator._errors import TurnAlreadyRunning
from cairn.orchestrator._middleware import ApprovalDecision, ApprovalRequest
from cairn.orchestrator._records import TurnRecord

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from cairn.config._models import ModelConfig
    from cairn.config._registry import ModelRegistry
    from cairn.domain import Session, UIEvent
    from cairn.orchestrator._clock import Clock
    from cairn.orchestrator._config import OrchestratorConfig
    from cairn.orchestrator._middleware import (
        MessagePreparer,
        ResultTransformer,
        ToolApprover,
        UIEventObserver,
    )
    from cairn.orchestrator._protocols import (
        ApprovalGateway,
        ContextManager,
        CostTracker,
        ExtractionQueue,
        MemoryService,
        Tool,
        ToolRegistry,
        ToolRunner,
    )
    from cairn.orchestrator._session_manager import SessionManager
    from cairn.persistence._messages_repo import MessageRepo
    from cairn.persistence._turns_repo import TurnRepo
    from cairn.providers._registry import ProviderRegistry


_logger = logging.getLogger("cairn.orchestrator")


class Orchestrator:
    """The turn loop.

    Composed of injected collaborator protocols; owns no downstream
    implementation. Holds per-session cancel flags and minimal in-memory
    state for the lifetime of a running turn.
    """

    def __init__(
        self,
        *,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        session_manager: SessionManager,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        tool_runner: ToolRunner,
        memory_service: MemoryService,
        extraction_queue: ExtractionQueue,
        approval_gateway: ApprovalGateway,
        cost_tracker: CostTracker,
        turn_repo: TurnRepo,
        message_repo: MessageRepo,
        clock: Clock,
        config: OrchestratorConfig,
        preparers: Sequence[MessagePreparer] = (),
        approvers: Sequence[ToolApprover] = (),
        transformers: Sequence[ResultTransformer] = (),
        observers: Sequence[UIEventObserver] = (),
    ) -> None:
        self._provider_registry = provider_registry
        self._model_registry = model_registry
        self._session_manager = session_manager
        self._context_manager = context_manager
        self._tool_registry = tool_registry
        self._tool_runner = tool_runner
        self._memory_service = memory_service
        self._extraction_queue = extraction_queue
        self._approval_gateway = approval_gateway
        self._cost_tracker = cost_tracker
        self._turn_repo = turn_repo
        self._message_repo = message_repo
        self._clock = clock
        self._config = config
        self._preparers = tuple(preparers)
        self._approvers = tuple(approvers)
        self._transformers = tuple(transformers)
        self._observers = tuple(observers)
        self._cancel_flags: dict[str, asyncio.Event] = {}

    # -- Session lifecycle ----------------------------------------------

    async def start_session(
        self,
        *,
        type: SessionType,
        persona: str,
        model: str | None = None,
        memory_space: str | None = None,
    ) -> Session:
        session = await self._session_manager.create(
            type=type,
            persona=persona,
            model=model,
            memory_space=memory_space,
        )
        self._fanout(SessionCreated(session_id=session.id))
        return session

    async def resume_session(self, session_id: str) -> Session:
        session = await self._session_manager.get(session_id)
        self._fanout(SessionResumed(session_id=session.id))
        return session

    async def archive_session(self, session_id: str) -> None:
        await self._session_manager.archive(session_id)
        self._fanout(SessionArchived(session_id=session_id))

    # -- Hot reload -----------------------------------------------------

    def replace_collaborators(
        self,
        *,
        provider_registry: ProviderRegistry | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        """Swap disk-derived collaborators in place.

        Used by `/reload` to apply a re-loaded config without
        re-bootstrapping the orchestrator. Only collaborators whose
        construction depends on `CairnConfig` are swappable here —
        runtime-state collaborators (turn repo, extraction queue,
        memory service, approval gateway) are preserved so in-flight
        work isn't disrupted.

        Each kwarg is optional so callers (and tests) can swap one
        collaborator at a time. The next iteration of any running
        turn will pick up the new instances; the current iteration
        continues with whatever was bound when it started.
        """
        if provider_registry is not None:
            self._provider_registry = provider_registry
        if model_registry is not None:
            self._model_registry = model_registry

    # -- Cancellation ---------------------------------------------------

    async def cancel(self, session_id: str) -> None:
        """Signal cancellation for an in-flight turn. Safe to call
        even if no turn is running — it's a no-op in that case."""
        flag = self._cancel_flags.get(session_id)
        if flag is not None:
            flag.set()

    # -- Crash recovery -------------------------------------------------

    async def resume_aborted_turns(self) -> list[str]:
        """Scan for turns left non-terminal by a prior crash. Mark them
        aborted. Returns the list of session IDs with dangling turns
        so the UI can surface 'resume from your message?' prompts."""
        _logger.info("resume_aborted_turns.scan_started")
        dangling = await self._turn_repo.list_non_terminal()
        session_ids: list[str] = []
        now = self._clock.now()
        for turn in dangling:
            await self._turn_repo.mark_aborted(turn.id, reason="process_crash", completed_at=now)
            session_ids.append(turn.session_id)
            _logger.info(
                "turn.resumed_as_aborted",
                extra={
                    "turn_id": turn.id,
                    "session_id": turn.session_id,
                    "prior_state": turn.state.value,
                    "reason": "process_crash",
                },
            )
        _logger.info(
            "resume_aborted_turns.scan_complete",
            extra={"resumed_count": len(dangling)},
        )
        return session_ids

    # -- Turn loop ------------------------------------------------------

    async def run_turn(
        self,
        session_id: str,
        user_msg: Message,
    ) -> AsyncIterator[UIEvent]:
        """Run a turn. Yields UIEvents as it progresses.

        Consumer semantics: each yielded event is delivered to all
        registered observers before returning to the caller (observer
        errors are logged and swallowed — they can't break a turn).
        """
        session = await self._session_manager.get(session_id)

        if session_id in self._cancel_flags:
            raise TurnAlreadyRunning(f"Session {session_id!r} already has a turn running")
        cancel_flag = asyncio.Event()
        self._cancel_flags[session_id] = cancel_flag
        # Mutable single-element flag the deadline watchdog flips before
        # firing the cancel. The CancelledError handler in _drive_turn
        # reads it to distinguish "wall-clock deadline" from
        # "user pressed ESC / Ctrl-C" — both arrive as CancelledError.
        timeout_marker: list[bool] = [False]
        watchdog = asyncio.create_task(
            self._turn_deadline_watchdog(
                deadline_s=self._config.max_turn_duration_s,
                cancel_flag=cancel_flag,
                timeout_marker=timeout_marker,
            ),
            name=f"cairn-turn-watchdog-{session_id}",
        )

        try:
            async for event in self._drive_turn(
                session, user_msg, cancel_flag, timeout_marker=timeout_marker
            ):
                self._fanout(event)
                yield event
        finally:
            # Always cancel the watchdog — guaranteed cleanup regardless
            # of how the turn loop exits (normal completion, TurnAborted,
            # exception). The watchdog itself swallows CancelledError.
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
            self._cancel_flags.pop(session_id, None)

    # -- Internal: the state machine -----------------------------------

    async def _drive_turn(
        self,
        session: Session,
        user_msg: Message,
        cancel_flag: asyncio.Event,
        *,
        timeout_marker: list[bool] | None = None,
    ) -> AsyncIterator[UIEvent]:
        turn_id = uuid.uuid4().hex

        # Pre-turn budget check. Happens *before* we persist anything,
        # so a blocked turn leaves no orphan rows.
        verdict = await self._cost_tracker.should_block_turn(session_id=session.id)
        if verdict is BudgetVerdict.BLOCK:
            yield TurnBlocked(
                session_id=session.id,
                turn_id=turn_id,
                reason="budget",
                message="Session budget cap reached. Archive or up the cap.",
            )
            return

        # Persist user message + turn row atomically-ish. The user_msg
        # carries its turn_id; the turns row carries the user_message_id.
        user_msg.session_id = session.id
        await self._message_repo.append(user_msg, turn_id=turn_id)
        started_at = self._clock.now()
        await self._turn_repo.insert(
            TurnRecord(
                id=turn_id,
                session_id=session.id,
                user_message_id=user_msg.id,
                state=TurnState.STARTED,
                iteration_count=0,
                model=session.model,
                started_at=started_at,
                completed_at=None,
                aborted_reason=None,
                stop_reason=None,
            )
        )
        yield UserMessagePersisted(message_id=user_msg.id, turn_id=turn_id)

        if verdict is BudgetVerdict.WARN:
            yield BudgetWarning(
                session_id=session.id,
                turn_id=turn_id,
                cost_usd=0.0,  # current session spend is reported via observability later
                threshold_usd=0.0,
            )

        try:
            # Memory retrieval
            await self._turn_repo.transition(
                turn_id,
                from_state=TurnState.STARTED,
                to_state=TurnState.MEMORY_RETRIEVAL,
            )
            retrieved = []
            if session.memory_space is not None:
                retrieved = await self._memory_service.retrieve(
                    space=session.memory_space,
                    query=user_msg.get_text(),
                    k=8,
                )

            # Iteration loop
            await self._turn_repo.transition(
                turn_id,
                from_state=TurnState.MEMORY_RETRIEVAL,
                to_state=TurnState.ITERATION,
            )

            stop_reason: StopReason | None = None
            model_cfg = self._model_registry.resolve(session.model)
            had_partial_tool_input = False

            for iteration in range(self._config.max_iterations):
                self._raise_if_cancelled(cancel_flag)

                # Capture turn_id in the closures — the orchestrator
                # owns _fanout, the DelegationTool owns the child
                # session id; we stitch them together here.
                def _on_spawned(child_id: str, turn_id: str = turn_id) -> None:
                    self._fanout(DelegationSpawned(session_id=child_id, turn_id=turn_id))

                def _on_completed(child_id: str, turn_id: str = turn_id) -> None:
                    self._fanout(DelegationCompleted(session_id=child_id, turn_id=turn_id))

                tctx = TurnContext(
                    session=session,
                    turn_id=turn_id,
                    iteration=iteration,
                    on_delegation_spawned=_on_spawned,
                    on_delegation_completed=_on_completed,
                )

                # Build request + apply preparer chain
                history = await self._message_repo.list_for_session(session.id)
                tools = self._tool_registry.for_session(session)
                request = await self._context_manager.build_request(
                    session=session,
                    history=history,
                    retrieved_memories=retrieved,
                    tools=tools,
                    cache_aware=model_cfg.supports_prompt_cache,
                )
                for preparer in self._preparers:
                    request = await preparer.prepare(request, tctx)

                # Stream from provider
                assistant_msg = Message(role="assistant", session_id=session.id)
                pending_tool_calls: list[ToolUseBlock] = []
                stream_start = self._clock.now()
                current_stop: StopReason | None = None
                pending_usage: list[UsageEvent] = []

                provider = self._provider_registry.for_model(model_cfg)
                async for ev in provider.stream(request):
                    self._raise_if_cancelled(cancel_flag)
                    match ev:
                        case TextDelta(text=t):
                            assistant_msg.append_text_delta(t)
                            yield AssistantTextDelta(
                                message_id=assistant_msg.id,
                                turn_id=turn_id,
                                text=t,
                            )
                        case ThinkingDelta(text=t):
                            assistant_msg.append_thinking_delta(t)
                        case ToolCallStart(id=tc_id, name=name):
                            assistant_msg.start_tool_use(tc_id, name)
                        case ToolCallDelta(id=tc_id, input_delta=chunk):
                            assistant_msg.append_tool_input_delta(tc_id, chunk)
                        case ToolCallEnd(id=tc_id):
                            assistant_msg.finalize_tool_use(tc_id)
                            pending_tool_calls.append(assistant_msg.get_tool_use(tc_id))
                        case UsageEvent() as u:
                            # Buffer usage events — the model_usage FK on
                            # message_id requires the assistant row to exist
                            # first. Recorded below, after persist.
                            pending_usage.append(u)
                        case MessageStop(stop_reason=sr):
                            current_stop = sr

                # Persist assistant message, then flush buffered usage rows.
                await self._message_repo.append(assistant_msg, turn_id=turn_id)
                stream_end = self._clock.now()
                duration_ms = int((stream_end - stream_start).total_seconds() * 1000)
                for u in pending_usage:
                    await self._cost_tracker.record(
                        session_id=session.id,
                        parent_session_id=session.parent_session_id,
                        turn_id=turn_id,
                        message_id=assistant_msg.id,
                        usage=u,
                        provider=provider.name,
                        model=model_cfg.id,
                        role="primary",
                        operation=UsageOperation.PRIMARY_TURN,
                        duration_ms=duration_ms,
                        cost_usd=self._compute_cost(model_cfg, u),
                    )

                yield AssistantMessageComplete(message_id=assistant_msg.id, turn_id=turn_id)

                stop_reason = current_stop
                had_partial_tool_input = assistant_msg.has_pending_tool_input()

                if not pending_tool_calls:
                    break

                # Tool dispatch
                await self._turn_repo.transition(
                    turn_id,
                    from_state=TurnState.ITERATION,
                    to_state=TurnState.TOOL_DISPATCH,
                )
                results = [
                    r
                    async for r in self._dispatch_tools(
                        tool_calls=pending_tool_calls,
                        session=session,
                        turn_id=turn_id,
                        tctx=tctx,
                        cancel_flag=cancel_flag,
                        assistant_msg_id=assistant_msg.id,
                    )
                ]
                # Extract just the ToolResultBlocks (yielded by _dispatch_tools
                # as (event | result, ...) tuples). See below.
                tool_results = [r[0] for r in results if isinstance(r, tuple)]
                # Re-emit any events queued by the dispatcher:
                for item in results:
                    if not isinstance(item, tuple):
                        yield item

                tool_msg = Message(
                    role="user",
                    session_id=session.id,
                    content=list(tool_results),
                )
                await self._message_repo.append(tool_msg, turn_id=turn_id)

                # Back to iteration
                await self._turn_repo.transition(
                    turn_id,
                    from_state=TurnState.TOOL_DISPATCH,
                    to_state=TurnState.ITERATION,
                )
                await self._turn_repo.increment_iteration(turn_id)

            # Finalise
            await self._turn_repo.transition(
                turn_id,
                from_state=TurnState.ITERATION,
                to_state=TurnState.FINALISING,
            )

            # Post-turn extraction (fire-and-forget, only if memory-bound).
            if session.memory_space is not None and session.type is not SessionType.EPHEMERAL:
                self._extraction_queue.submit(
                    session_id=session.id,
                    since_idx=user_msg.idx,
                    turn_id=turn_id,
                )
                await self._turn_repo.transition(
                    turn_id,
                    from_state=TurnState.FINALISING,
                    to_state=TurnState.EXTRACTION_ENQUEUED,
                )
                yield ObservationExtractionRequested(session_id=session.id, turn_id=turn_id)

            final_stop = stop_reason or StopReason.END_TURN
            await self._turn_repo.mark_completed(
                turn_id,
                stop_reason=final_stop,
                completed_at=self._clock.now(),
            )
            if final_stop is StopReason.MAX_TOKENS and had_partial_tool_input:
                yield TurnIncomplete(session_id=session.id, turn_id=turn_id)
            else:
                yield TurnComplete(session_id=session.id, turn_id=turn_id, stop_reason=final_stop)

        except asyncio.CancelledError:
            # Distinguish wall-clock deadline from user cancel — both
            # arrive here as CancelledError because the deadline
            # watchdog cancels via the same flag the UI uses for ESC.
            cancel_reason = (
                "turn_timeout" if (timeout_marker and timeout_marker[0]) else "user_cancel"
            )
            await self._turn_repo.mark_aborted(
                turn_id,
                reason=cancel_reason,
                completed_at=self._clock.now(),
            )
            yield TurnAborted(
                session_id=session.id,
                turn_id=turn_id,
                reason=cancel_reason,
            )
        except BudgetOverflowDeclined:
            await self._turn_repo.mark_aborted(
                turn_id,
                reason="user_declined_overflow",
                completed_at=self._clock.now(),
            )
            try:
                await self._session_manager.archive(session.id)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "compaction.archive_failed",
                    extra={"turn_id": turn_id, "session_id": session.id},
                )
            yield TurnAborted(
                session_id=session.id,
                turn_id=turn_id,
                reason="user_declined_overflow",
            )
            yield SessionArchived(session_id=session.id)
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "turn.failed",
                extra={"turn_id": turn_id, "session_id": session.id},
            )
            await self._turn_repo.mark_aborted(
                turn_id,
                reason="error",
                completed_at=self._clock.now(),
            )
            yield TurnAborted(
                session_id=session.id,
                turn_id=turn_id,
                reason="error",
                error_class=ErrorClass.UNEXPECTED,
                message=str(exc),
            )

    # -- Internal helpers -----------------------------------------------

    async def _dispatch_tools(
        self,
        *,
        tool_calls: list[ToolUseBlock],
        session: Session,
        turn_id: str,
        tctx: TurnContext,
        cancel_flag: asyncio.Event,
        assistant_msg_id: str,
    ):
        """Dispatch each tool call through the approval chain + runner.

        Yields a mix of `UIEvent` (to be re-yielded upward) and
        `(ToolResultBlock,)` one-tuples (results for the model).
        The caller reassembles the stream.

        Responsibility split with the runner:
        - Orchestrator owns approval chain + UI events (Planned, Approved,
          Rejected, Started, Completed) + transformer application + timing.
        - Runner owns `tool_calls` / `approval_decisions` DB writes,
          `asyncio.timeout` wrapping, and error-to-error-block synthesis.
          It is called once per tool call — on both approve and reject —
          so the audit trail always lands.
        """
        for tool_call in tool_calls:
            self._raise_if_cancelled(cancel_flag)
            tool = self._tool_registry.get(tool_call.name)

            # Unknown tool → inject error result; no UI events for it
            # because it never reached the planned state cleanly.
            if tool is None:
                yield (
                    ToolResultBlock(
                        tool_use_id=tool_call.id,
                        content=f"Unknown tool: {tool_call.name!r}",
                        is_error=True,
                    ),
                )
                continue

            yield ToolCallPlanned(
                tool_call_id=tool_call.id,
                turn_id=turn_id,
                tool_name=tool_call.name,
                args=dict(tool_call.input),
            )

            decision = await self._decide_approval(tool_call, tool, tctx)

            if decision.outcome is ApprovalOutcome.REJECT:
                yield ToolCallRejected(
                    tool_call_id=tool_call.id,
                    turn_id=turn_id,
                    decided_by=decision.decided_by,
                    reason=decision.reason,
                )
                # Runner still writes the rejection to tool_calls +
                # approval_decisions and returns the error block.
                result = await self._tool_runner.run(
                    tool_call=tool_call,
                    tool=tool,
                    session=session,
                    turn_id=turn_id,
                    ctx=tctx,
                    decision=decision,
                    message_id=assistant_msg_id,
                )
                yield (result,)
                continue

            yield ToolCallApproved(
                tool_call_id=tool_call.id,
                turn_id=turn_id,
                approved_by=decision.decided_by,
            )
            yield ToolCallStarted(
                tool_call_id=tool_call.id,
                turn_id=turn_id,
                tool_name=tool_call.name,
            )

            started = self._clock.now()
            result = await self._tool_runner.run(
                tool_call=tool_call,
                tool=tool,
                session=session,
                turn_id=turn_id,
                ctx=tctx,
                decision=decision,
                message_id=assistant_msg_id,
            )
            duration_ms = int((self._clock.now() - started).total_seconds() * 1000)

            # Run transformers (spotlighting, redaction, Unicode strip).
            for transformer in self._transformers:
                result = await transformer.transform(result, tool_call, tctx)

            status = ToolCallStatus.FAILED if result.is_error else ToolCallStatus.COMPLETED
            yield ToolCallCompleted(
                tool_call_id=tool_call.id,
                turn_id=turn_id,
                status=status,
                is_error=result.is_error,
                duration_ms=duration_ms,
            )
            yield (result,)

    async def _decide_approval(
        self,
        tool_call: ToolUseBlock,
        tool: Tool,
        tctx: TurnContext,
    ) -> ApprovalDecision:
        if not tool.approval_required:
            return ApprovalDecision(
                outcome=ApprovalOutcome.APPROVE,
                decided_by="auto:no-approval-required",
            )

        request = ApprovalRequest(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=dict(tool_call.input),
            risk_tier=tool.risk_tier,
            side_effects=tool.side_effects,
        )
        for approver in self._approvers:
            decision = await approver.decide(request, tctx)
            if decision.outcome is not ApprovalOutcome.ESCALATE:
                return decision
        # Chain exhausted or empty → gateway.
        return await self._approval_gateway.request(
            session_id=tctx.session.id,
            tool_call_id=tool_call.id,
            request=request,
        )

    def _fanout(self, event: UIEvent) -> None:
        for obs in self._observers:
            try:
                obs.observe(event)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "observer.failed",
                    extra={"observer": type(obs).__name__},
                )

    @staticmethod
    def _raise_if_cancelled(flag: asyncio.Event) -> None:
        if flag.is_set():
            raise asyncio.CancelledError()

    @staticmethod
    async def _turn_deadline_watchdog(
        *,
        deadline_s: float,
        cancel_flag: asyncio.Event,
        timeout_marker: list[bool],
    ) -> None:
        """Soft-cancel the turn after `deadline_s` seconds of wall-clock.

        Cancellation is observed at the next iteration boundary inside
        the orchestrator (between tool calls / between LLM round-trips).
        Tools already in flight finish — the per-tool `timeout_s`
        bounds them independently. The marker lets the
        `CancelledError` handler distinguish "deadline hit" from
        "user pressed ESC" (both fire `cancel_flag.set()`).
        """
        try:
            await asyncio.sleep(deadline_s)
        except asyncio.CancelledError:
            return
        if not cancel_flag.is_set():
            timeout_marker[0] = True
            cancel_flag.set()

    @staticmethod
    def _compute_cost(model: ModelConfig, usage: UsageEvent) -> float:
        """Compute per-call cost from tokens + model pricing."""
        cost = (
            usage.input_tokens * model.input_cost_per_1m
            + usage.output_tokens * model.output_cost_per_1m
        )
        if usage.cache_read_tokens and model.cache_read_cost_per_1m:
            cost += usage.cache_read_tokens * model.cache_read_cost_per_1m
        if usage.cache_write_tokens and model.cache_write_cost_per_1m:
            cost += usage.cache_write_tokens * model.cache_write_cost_per_1m
        return cost / 1_000_000
