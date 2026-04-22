"""Observation extractor — the engine behind the memory brick's
post-turn extraction.

The `Extractor` is a pure coroutine: given a transcript and an anchor
(session_id + source_message_id + turn_id), it calls the extraction
model, parses the structured response, stores each observation via
`MemoryRepo`, mirrors it to `ObservationLog`, and records usage via the
orchestrator's `CostTracker`.

The queue layer (`ObservationExtractionQueue`) builds transcripts,
applies length / persona / tool-only gates, and invokes the extractor.
This module deliberately knows nothing about queues so tests can drive
it directly.

Design: `.plan/memory-brick-design.md` Phase 3.
"""

from __future__ import annotations

import json
import logging
import uuid
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from cairn.config._models import ModelRole
from cairn.config._registry import ModelNotFoundError
from cairn.domain._enums import (
    MemoryClass,
    MemoryEntryType,
    StopReason,
    UsageOperation,
)
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    ProviderRequest,
    TextDelta,
    UsageEvent,
)
from cairn.memory._observation_log import Observation

if TYPE_CHECKING:
    from datetime import datetime

    from cairn.config._models import MemoryConfig, ModelConfig
    from cairn.config._registry import ModelRegistry
    from cairn.memory._observation_log import ObservationLog
    from cairn.orchestrator._clock import Clock
    from cairn.orchestrator._protocols import CostTracker
    from cairn.persistence._memory_repo import MemoryRepo
    from cairn.providers._registry import ProviderRegistry


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


_EPISODIC_TYPES: frozenset[MemoryEntryType] = frozenset(
    {MemoryEntryType.EVENT, MemoryEntryType.TASK}
)


class ExtractedObservation(BaseModel):
    """A single observation as emitted by the extraction model."""

    content: str
    entry_type: MemoryEntryType
    importance: int = Field(ge=1, le=10)


class ExtractionResponse(BaseModel):
    """The whole model response envelope."""

    observations: list[ExtractedObservation] = []


def _memory_class_for(entry_type: MemoryEntryType) -> MemoryClass:
    """Map an entry type to the memory class used for recency decay."""
    return MemoryClass.EPISODIC if entry_type in _EPISODIC_TYPES else MemoryClass.SEMANTIC


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


@cache
def _load_system_prompt() -> str:
    """Load the extraction system prompt from the packaged template.

    Cached at module load — the prompt file doesn't change between
    process restarts, and re-reading on every extraction is pure waste.
    """
    return (
        resources.files("cairn.memory._prompts").joinpath("extract.md").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Transcript rendering
# ---------------------------------------------------------------------------


def render_transcript(
    *,
    context_messages: list[Message],
    latest_messages: list[Message],
) -> str:
    """Render transcript text for the extraction prompt.

    Each line is prefixed `[context]` or `[LATEST]` + role, mirroring the
    example in `extract.md`. Tool calls are summarised inline.
    """
    lines: list[str] = []
    for msg in context_messages:
        lines.append(_render_message("[context]", msg))
    for msg in latest_messages:
        lines.append(_render_message("[LATEST]", msg))
    return "\n".join(lines)


def _render_message(prefix: str, msg: Message) -> str:
    """Render one message line, summarising tool interactions."""
    role = msg.role.capitalize()
    body = msg.get_text().strip()
    chunks: list[str] = []
    if body:
        chunks.append(body)
    for block in msg.content:
        kind = getattr(block, "type", None)
        if kind == "tool_use":
            name = getattr(block, "name", "?")
            chunks.append(f"[tool: {name}]")
        elif kind == "tool_result":
            text = _summarise_tool_result(block)
            truncated = text[:200] + ("…" if len(text) > 200 else "")
            chunks.append(f"[tool_result: {truncated}]")

    body_str = " ".join(chunks) if chunks else "(no text)"
    return f"{prefix} {role}: {body_str}"


def _summarise_tool_result(block: object) -> str:
    """Pull a readable summary out of a ToolResultBlock's content."""
    content = getattr(block, "content", None)
    if isinstance(content, list):
        items: list[object] = content  # type: ignore[assignment]
        for item in items:
            if getattr(item, "type", None) == "text":
                text = getattr(item, "text", "")
                if isinstance(text, str):
                    return text
        return ""
    if content is None:
        return ""
    return str(content)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ExtractionResult(BaseModel):
    """Summary of what happened in an extraction call.

    Mostly useful for tests and future /memory introspection. The side
    effects (writes to MemoryRepo + ObservationLog + CostTracker) are
    the primary output.
    """

    model_config = {"frozen": True}

    observations_written: int = 0
    cost_usd: float = 0.0
    truncated_by_cost_cap: bool = False
    parse_failed: bool = False


class Extractor:
    """Engine for one extraction call.

    Collaborators are injected; the object is safe to reuse across many
    extractions (it holds no per-call state). The queue wraps this with
    gating + dispatch logic.
    """

    def __init__(
        self,
        *,
        memory_repo: MemoryRepo,
        observation_log: ObservationLog,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        cost_tracker: CostTracker,
        clock: Clock,
        memory_config: MemoryConfig,
    ) -> None:
        self._memory_repo = memory_repo
        self._observation_log = observation_log
        self._provider_registry = provider_registry
        self._model_registry = model_registry
        self._cost_tracker = cost_tracker
        self._clock = clock
        self._config = memory_config

    # ------------------------------------------------------------------

    def resolve_model(self) -> ModelConfig:
        """Return the model to use for extraction.

        Falls back to `UTILITY` when no model carries the `EXTRACTION`
        role, so profiles that haven't opted in to a dedicated extractor
        keep working.
        """
        try:
            return self._model_registry.by_role(ModelRole.EXTRACTION)
        except ModelNotFoundError:
            return self._model_registry.by_role(ModelRole.UTILITY)

    # ------------------------------------------------------------------

    async def extract(
        self,
        *,
        memory_space: str,
        turn_id: str,
        source_session_id: str,
        source_message_id: str,
        context_messages: list[Message],
        latest_messages: list[Message],
    ) -> ExtractionResult:
        """Run one extraction pass.

        Errors are logged and swallowed — the worker must keep running.
        The return value is informational.
        """
        transcript = render_transcript(
            context_messages=context_messages,
            latest_messages=latest_messages,
        )

        prompt_msg = Message(
            id=f"extract-{uuid.uuid4()}",
            role="user",
            session_id=source_session_id,
        )
        prompt_msg.append_text_delta(transcript)

        model_cfg = self.resolve_model()
        provider = self._provider_registry.for_model(model_cfg)

        request = ProviderRequest(
            model=model_cfg.id,
            messages=[prompt_msg],
            system=_load_system_prompt(),
            max_tokens=model_cfg.max_output_tokens,
            temperature=0.0,
        )

        text_buffer: list[str] = []
        usage_total = UsageEvent(input_tokens=0, output_tokens=0)
        cost_so_far = 0.0
        truncated = False
        started_at = self._clock.now()

        try:
            async for event in provider.stream(request):
                if isinstance(event, TextDelta):
                    text_buffer.append(event.text)
                elif isinstance(event, UsageEvent):
                    usage_total = _merge_usage(usage_total, event)
                    cost_so_far = _compute_cost(model_cfg, usage_total)
                    if cost_so_far >= self._config.max_extraction_cost_usd:
                        log.warning(
                            "extraction truncated by cost cap: turn_id=%s cost=%.4f cap=%.4f",
                            turn_id,
                            cost_so_far,
                            self._config.max_extraction_cost_usd,
                        )
                        truncated = True
                        break
                elif isinstance(event, MessageStop):
                    if event.stop_reason == StopReason.MAX_TOKENS:
                        truncated = True
                    break
        except Exception:  # noqa: BLE001
            log.exception("extractor: provider.stream failed for turn_id=%s", turn_id)
            return ExtractionResult(parse_failed=True)

        raw_text = "".join(text_buffer).strip()
        parsed = _parse_response(raw_text)
        if parsed is None:
            log.warning(
                "extractor: failed to parse response for turn_id=%s; raw=%r",
                turn_id,
                raw_text[:500],
            )
            await self._record_usage(
                turn_id=turn_id,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                usage=usage_total,
                model_cfg=model_cfg,
                provider_name=provider.name,
                duration_ms=_elapsed_ms(started_at, self._clock.now()),
                cost_usd=cost_so_far,
            )
            return ExtractionResult(
                parse_failed=True,
                cost_usd=cost_so_far,
                truncated_by_cost_cap=truncated,
            )

        written = 0
        for obs in parsed.observations:
            try:
                stored = await self._memory_repo.store(
                    memory_space=memory_space,
                    content=obs.content,
                    entry_type=obs.entry_type,
                    memory_class=_memory_class_for(obs.entry_type),
                    importance=obs.importance,
                    source_session_id=source_session_id,
                    source_message_id=source_message_id,
                )
                await self._observation_log.append(Observation.from_entry(stored))
                written += 1
            except Exception:  # noqa: BLE001
                log.exception(
                    "extractor: failed to store observation; turn_id=%s content=%r",
                    turn_id,
                    obs.content[:100],
                )

        await self._record_usage(
            turn_id=turn_id,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            usage=usage_total,
            model_cfg=model_cfg,
            provider_name=provider.name,
            duration_ms=_elapsed_ms(started_at, self._clock.now()),
            cost_usd=cost_so_far,
        )

        return ExtractionResult(
            observations_written=written,
            cost_usd=cost_so_far,
            truncated_by_cost_cap=truncated,
        )

    # ------------------------------------------------------------------

    async def _record_usage(
        self,
        *,
        turn_id: str,
        source_session_id: str,
        source_message_id: str,
        usage: UsageEvent,
        model_cfg: ModelConfig,
        provider_name: str,
        duration_ms: int,
        cost_usd: float,
    ) -> None:
        try:
            await self._cost_tracker.record(
                session_id=source_session_id,
                parent_session_id=None,
                turn_id=turn_id,
                message_id=source_message_id,
                usage=usage,
                provider=provider_name,
                model=model_cfg.id,
                role=ModelRole.EXTRACTION.value,
                operation=UsageOperation.EXTRACTION,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
            )
        except Exception:  # noqa: BLE001
            log.exception("extractor: cost-tracker record failed; turn_id=%s", turn_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_response(raw: str) -> ExtractionResponse | None:
    """Parse the model response into `ExtractionResponse`.

    Tolerates the common "model wrapped the JSON in a ```json fence"
    failure mode; anything else returns None and lets the caller log +
    drop the batch.
    """
    if not raw:
        return None
    candidate = _strip_code_fence(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    try:
        return ExtractionResponse.model_validate(data)
    except ValidationError:
        return None


def _strip_code_fence(raw: str) -> str:
    """Remove a leading/trailing ```json fence if present."""
    s = raw.strip()
    if s.startswith("```"):
        # Drop the opening fence line, and the trailing ``` if present.
        newline = s.find("\n")
        if newline != -1:
            s = s[newline + 1 :]
        if s.endswith("```"):
            s = s[: -len("```")]
    return s.strip()


def _merge_usage(a: UsageEvent, b: UsageEvent) -> UsageEvent:
    """Sum two UsageEvents (providers stream incremental usage)."""
    return UsageEvent(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )


def _compute_cost(model: ModelConfig, usage: UsageEvent) -> float:
    """Per-call cost — duplicate of the orchestrator's helper.

    Kept local to avoid importing from `cairn.orchestrator` (which
    would create a cycle).
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


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)
