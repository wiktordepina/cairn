"""CostTracker — cost recording and budget enforcement.

Wraps `UsageRepo` for writes and implements `should_block_turn`
against the profile's `BudgetConfig`. All timestamp reads go through
the injected `Clock` so tests can pin time deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.orchestrator._enums import BudgetVerdict

if TYPE_CHECKING:
    from cairn.config._models import BudgetConfig
    from cairn.domain._enums import UsageOperation
    from cairn.domain._provider import UsageEvent
    from cairn.orchestrator._clock import Clock
    from cairn.persistence._usage_repo import UsageRepo


class BasicCostTracker:
    """Default `CostTracker` implementation.

    - `record` delegates to `UsageRepo.record`, stamping the turn_id
      so per-turn aggregations work downstream.
    - `should_block_turn` returns `BLOCK` when *previously-recorded*
      spend already meets or exceeds the session or daily cap, `WARN`
      when session spend has crossed the configured warn fraction of
      the per-session cap, and `PROCEED` otherwise.

    Budget checks reflect costs *already persisted* — the turn currently
    starting is not counted. The orchestrator's own `max_iterations`
    guards against unbounded loops within a single turn.
    """

    def __init__(
        self,
        *,
        usage_repo: UsageRepo,
        clock: Clock,
        budgets: BudgetConfig,
        warn_threshold_fraction: float = 0.8,
        profile: str | None = None,
    ) -> None:
        if not 0 < warn_threshold_fraction <= 1:
            raise ValueError(
                f"warn_threshold_fraction must be in (0, 1]; got {warn_threshold_fraction}"
            )
        self._usage_repo = usage_repo
        self._clock = clock
        self._budgets = budgets
        self._warn_fraction = warn_threshold_fraction
        self._profile = profile

    async def record(
        self,
        *,
        session_id: str,
        parent_session_id: str | None,
        turn_id: str,
        message_id: str | None,
        usage: UsageEvent,
        provider: str,
        model: str,
        role: str,
        operation: UsageOperation,
        duration_ms: int | None,
        cost_usd: float,
    ) -> None:
        await self._usage_repo.record(
            timestamp=self._clock.now(),
            session_id=session_id,
            message_id=message_id,
            turn_id=turn_id,
            parent_session_id=parent_session_id,
            provider=provider,
            model=model,
            role=role,
            operation=operation,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            profile=self._profile,
        )

    async def should_block_turn(self, *, session_id: str) -> BudgetVerdict:
        now = self._clock.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        daily_cost = await self._usage_repo.cost_in_window(since=midnight)
        if daily_cost >= self._budgets.daily_usd:
            return BudgetVerdict.BLOCK

        session_cost = await self._usage_repo.total_cost_for_session(session_id)
        if session_cost >= self._budgets.per_session_usd:
            return BudgetVerdict.BLOCK

        session_threshold = self._budgets.per_session_usd * self._warn_fraction
        if session_cost >= session_threshold:
            return BudgetVerdict.WARN

        return BudgetVerdict.PROCEED
