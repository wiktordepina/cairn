"""`TruncatingCompactor` — drops oldest turn blocks until the provider
request fits the compaction budget.

See `.plan/compaction-brick-design.md` §7 for the algorithm and §3.3
for the advisory-budget semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from cairn.compaction._errors import BudgetOverflowDeclined
from cairn.compaction._gateway import AutoTerminateOverflowGateway
from cairn.compaction._turns import iter_turn_blocks
from cairn.domain import (
    BudgetOverflowAdvisory,
    HistoryCompacted,
    ProviderRequest,
    UIEvent,
)

if TYPE_CHECKING:
    from cairn.compaction._gateway import BudgetOverflowGateway
    from cairn.config import CompactionConfig, ModelRegistry
    from cairn.orchestrator._context import TurnContext
    from cairn.providers import ProviderRegistry


log = logging.getLogger(__name__)


EventSink = Callable[[UIEvent], Awaitable[None]]


class TruncatingCompactor:
    """Message preparer that drops oldest turn blocks to fit a budget.

    Implements the `MessagePreparer` protocol from
    `cairn.orchestrator._middleware`. The preparer is stateless per
    turn — any decision memory (e.g. "user already accepted overflow
    for this session") lives on the `BudgetOverflowGateway`.
    """

    def __init__(
        self,
        config: CompactionConfig,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        *,
        overflow_gateway: BudgetOverflowGateway | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._config = config
        self._models = model_registry
        self._providers = provider_registry
        self._overflow_gateway = overflow_gateway or AutoTerminateOverflowGateway()
        self._event_sink = event_sink

    async def prepare(
        self,
        request: ProviderRequest,
        ctx: TurnContext,
    ) -> ProviderRequest:
        if not self._config.enabled:
            return request

        model_cfg = self._models.by_id(request.model)
        effective_budget = (
            model_cfg.context_window - request.max_tokens - self._config.safety_margin_tokens
        )
        if effective_budget < self._config.min_history_tokens:
            log.error(
                "compaction: effective budget %d below min_history_tokens %d "
                "(context_window=%d, max_tokens=%d, safety_margin=%d); "
                "passing request through unchanged",
                effective_budget,
                self._config.min_history_tokens,
                model_cfg.context_window,
                request.max_tokens,
                self._config.safety_margin_tokens,
            )
            return request

        provider = self._providers.for_model(model_cfg)
        tokens_before = await provider.count_tokens(request)
        if tokens_before <= effective_budget:
            log.debug(
                "compaction: request fits (tokens=%d budget=%d)",
                tokens_before,
                effective_budget,
            )
            return request

        blocks = list(iter_turn_blocks(request.messages))
        preserve_floor = max(0, len(blocks) - self._config.preserve_last_n_turns)

        # Try dropping blocks one at a time from the front while
        # respecting the preserve-floor.
        trimmed = request
        tokens_after = tokens_before
        blocks_dropped = 0
        messages_dropped = 0
        for drop_through in range(preserve_floor):
            messages_dropped = blocks[drop_through].end
            candidate_messages = list(request.messages[messages_dropped:])
            candidate = replace(request, messages=candidate_messages)
            tokens_after = await provider.count_tokens(candidate)
            blocks_dropped = drop_through + 1
            trimmed = candidate
            if tokens_after <= effective_budget:
                await self._emit_compacted(
                    ctx,
                    blocks_dropped=blocks_dropped,
                    messages_dropped=messages_dropped,
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    reason="budget",
                )
                return trimmed

        # Preserve-floor reached — surviving tail may still be over
        # budget. Advise the user.
        overflow = max(0, tokens_after - effective_budget)
        advisory = BudgetOverflowAdvisory(
            session_id=ctx.session.id,
            turn_id=ctx.turn_id,
            tokens_projected=tokens_after + request.max_tokens,
            context_window=model_cfg.context_window,
            safety_margin=self._config.safety_margin_tokens,
            overflow_tokens=overflow,
            will_fit_context_window=(tokens_after + request.max_tokens)
            <= model_cfg.context_window,
        )
        log.warning(
            "compaction: preserve-floor hit for session=%s turn=%s "
            "(tokens_after=%d budget=%d overflow=%d will_fit=%s); consulting gateway",
            ctx.session.id,
            ctx.turn_id,
            tokens_after,
            effective_budget,
            overflow,
            advisory.will_fit_context_window,
        )
        await self._emit(advisory)

        decision = await self._overflow_gateway.decide(advisory, ctx)
        log.info(
            "compaction: overflow decision for session=%s turn=%s => %s",
            ctx.session.id,
            ctx.turn_id,
            decision,
        )
        if decision == "terminate":
            raise BudgetOverflowDeclined(session_id=ctx.session.id, turn_id=ctx.turn_id)

        await self._emit_compacted(
            ctx,
            blocks_dropped=blocks_dropped,
            messages_dropped=messages_dropped,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            reason="preserve_floor_hit",
        )
        return trimmed

    async def _emit_compacted(
        self,
        ctx: TurnContext,
        *,
        blocks_dropped: int,
        messages_dropped: int,
        tokens_before: int,
        tokens_after: int,
        reason: str,
    ) -> None:
        log.info(
            "compaction: compacted session=%s turn=%s blocks=%d messages=%d "
            "tokens=%d->%d reason=%s",
            ctx.session.id,
            ctx.turn_id,
            blocks_dropped,
            messages_dropped,
            tokens_before,
            tokens_after,
            reason,
        )
        await self._emit(
            HistoryCompacted(
                session_id=ctx.session.id,
                turn_id=ctx.turn_id,
                blocks_dropped=blocks_dropped,
                messages_dropped=messages_dropped,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                reason=reason,
            )
        )

    async def _emit(self, event: UIEvent) -> None:
        sink = self._event_sink
        if sink is None:
            return
        await sink(event)
