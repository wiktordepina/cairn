"""`DelegationTool` — consult another model in an ephemeral sub-session.

Concrete `Tool` implementation matching the arch spec §4.9 and the
design in `.plan/tool-system-design.md` §12. Unlike the built-ins,
this tool is instantiated directly (not via `@tool`) because it needs
access to the session manager, provider + model registries, and cost
tracker.

Per-call flow (`invoke`):

    1. Create an ephemeral sub-session with `parent_session_id=ctx.session.id`.
    2. Build a single user `Message` from `args.prompt`.
    3. Stream `provider.stream(request)`, accumulating text + usage.
    4. On each `UsageEvent`, compute cost and break early if
       `config.max_cost_usd` is configured and exceeded.
    5. Record final `UsageEvent` via `cost_tracker` with
       `operation=DELEGATION` and `parent_session_id=ctx.session.id`.
    6. Archive the sub-session.
    7. Return `ToolResultBlock` with the accumulated text.

`DelegationSpawned` / `DelegationCompleted` UI events are **not**
emitted here — the orchestrator detects `isinstance(tool, DelegationTool)`
and emits them around the runner call (design-doc §16.Q2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from cairn.domain._content import TextBlock, ToolResultBlock
from cairn.domain._enums import SessionType, UsageOperation
from cairn.domain._messages import Message
from cairn.domain._provider import ProviderRequest, TextDelta, UsageEvent

if TYPE_CHECKING:
    from cairn.config._models import DelegationToolConfig, ModelConfig
    from cairn.config._registry import ModelRegistry
    from cairn.orchestrator._clock import Clock
    from cairn.orchestrator._context import TurnContext
    from cairn.orchestrator._protocols import CostTracker
    from cairn.orchestrator._session_manager import SessionManager
    from cairn.providers._registry import ProviderRegistry


class DelegationArgs(BaseModel):
    """Input schema for a delegation call."""

    prompt: str


class DelegationTool:
    """Consult another model in an ephemeral sub-session.

    `side_effects = "read"` — delegation consumes tokens but makes no
    external mutation. `risk_tier = 3` reflects that delegation
    spends money, per security-doc tiering.
    """

    tool_kind: Literal["native", "mcp", "delegation"] = "delegation"
    risk_tier: int = 3
    side_effects: Literal["none", "read", "write"] = "read"

    def __init__(
        self,
        *,
        config: DelegationToolConfig,
        session_manager: SessionManager,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: CostTracker,
        clock: Clock,
        timeout_s: float = 120.0,
    ) -> None:
        if config.preserve_history:
            raise NotImplementedError(
                "DelegationToolConfig.preserve_history is not yet supported; "
                "the sub-session starts empty in V1."
            )
        self._config = config
        self._session_manager = session_manager
        self._provider_registry = provider_registry
        self._model_registry = model_registry
        self._cost_tracker = cost_tracker
        self._clock = clock
        self._timeout_s = timeout_s
        self._input_schema = DelegationArgs.model_json_schema()

    # Tool protocol -----------------------------------------------------------

    @property
    def name(self) -> str:
        return self._config.tool_name

    @property
    def description(self) -> str:
        return self._config.description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def approval_required(self) -> bool:
        return self._config.approval_required

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    async def invoke(
        self,
        args: dict[str, object],
        ctx: TurnContext,
    ) -> ToolResultBlock:
        parsed = DelegationArgs.model_validate(args)
        model_cfg = self._model_registry.resolve(self._config.target_model)
        provider = self._provider_registry.for_model(model_cfg)

        sub_session = await self._session_manager.create(
            type=SessionType.EPHEMERAL,
            persona="_delegation",
            model=model_cfg.id,
            parent_session_id=ctx.session.id,
        )

        user_msg = Message(role="user", session_id=sub_session.id)
        user_msg.content.append(TextBlock(text=parsed.prompt))

        request = ProviderRequest(
            model=model_cfg.id,
            messages=[user_msg],
            system=self._config.sub_system_prompt,
            max_tokens=model_cfg.max_output_tokens,
        )

        accumulated_text: list[str] = []
        final_usage: UsageEvent | None = None
        capped = False
        stream_start = self._clock.now()

        try:
            async for event in provider.stream(request):
                if isinstance(event, TextDelta):
                    accumulated_text.append(event.text)
                elif isinstance(event, UsageEvent):
                    final_usage = event
                    if (
                        self._config.max_cost_usd is not None
                        and _compute_cost(model_cfg, event) > self._config.max_cost_usd
                    ):
                        capped = True
                        break
        finally:
            await self._session_manager.archive(sub_session.id)

        if final_usage is not None:
            duration_ms = int((self._clock.now() - stream_start).total_seconds() * 1000)
            await self._cost_tracker.record(
                session_id=sub_session.id,
                parent_session_id=ctx.session.id,
                turn_id=ctx.turn_id,
                message_id=None,
                usage=final_usage,
                provider=provider.name,
                model=model_cfg.id,
                role="delegation",
                operation=UsageOperation.DELEGATION,
                duration_ms=duration_ms,
                cost_usd=_compute_cost(model_cfg, final_usage),
            )

        text = "".join(accumulated_text)
        if capped:
            text += "\n\n[delegation: cost cap reached; output truncated]"

        return ToolResultBlock(
            tool_use_id="",  # filled in by the runner / orchestrator wiring
            content=text,
            is_error=False,
        )


def _compute_cost(model: ModelConfig, usage: UsageEvent) -> float:
    """Compute cost in USD from token counts + per-1M pricing.

    Mirrors `Orchestrator._compute_cost`. Duplicated rather than
    extracted because the shared helper's natural home (a pricing module)
    doesn't exist yet; extraction can wait until a third caller appears.
    """
    cost = (
        usage.input_tokens * model.input_cost_per_1m
        + usage.output_tokens * model.output_cost_per_1m
    )
    if usage.cache_read_tokens and model.cache_read_cost_per_1m:
        cost += usage.cache_read_tokens * model.cache_read_cost_per_1m
    if usage.cache_write_tokens and model.cache_write_cost_per_1m:
        cost += usage.cache_write_tokens * model.cache_write_cost_per_1m
    return cost / 1_000_000
