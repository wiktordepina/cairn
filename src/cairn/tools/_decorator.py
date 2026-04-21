"""The ``@tool`` decorator.

Wraps an async callable ``(args_model, ctx) -> str | list[ContentBlock]``
into a concrete ``Tool`` instance. The decorator:

- Validates risk_tier / side_effects combinations.
- Generates ``input_schema`` from the Pydantic args model.
- Produces a ``_DecoratedTool`` instance implementing the ``Tool``
  protocol (``cairn.orchestrator._protocols.Tool``).

Decorated tools are not auto-registered anywhere — the CLI assembles a
``DefaultToolRegistry`` from an explicit list at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cairn.domain._content import ContentBlock, TextBlock, ToolResultBlock

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import BaseModel

    from cairn.orchestrator._context import TurnContext


# User functions accept a validated args model + turn context, return the
# tool's output. The decorator wraps the return value into a ToolResultBlock.
_UserToolFn = "Callable[[Any, TurnContext], Awaitable[str | list[ContentBlock]]]"

_ALLOWED_RISK_TIERS: frozenset[int] = frozenset({0, 1, 2, 3, 4})
_READER_SIDE_EFFECTS: frozenset[str] = frozenset({"none", "read"})


@dataclass(slots=True, frozen=True)
class _ToolSpec:
    """The full metadata bundle for a decorated tool."""

    name: str
    description: str
    risk_tier: int
    side_effects: Literal["none", "read", "write"]
    timeout_s: float
    approval_required: bool
    tool_kind: Literal["native", "mcp", "delegation"]
    args_model: type[BaseModel]
    input_schema: dict[str, Any]


class _DecoratedTool:
    """Concrete ``Tool`` built by the ``@tool`` decorator.

    Satisfies ``cairn.orchestrator._protocols.Tool``.
    """

    def __init__(
        self,
        spec: _ToolSpec,
        fn: Callable[..., Awaitable[str | list[ContentBlock]]],
    ) -> None:
        self._spec = spec
        self._fn = fn

    # Tool protocol properties --------------------------------------------

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._spec.input_schema

    @property
    def tool_kind(self) -> Literal["native", "mcp", "delegation"]:
        return self._spec.tool_kind

    @property
    def approval_required(self) -> bool:
        return self._spec.approval_required

    @property
    def risk_tier(self) -> int:
        return self._spec.risk_tier

    @property
    def side_effects(self) -> Literal["none", "read", "write"]:
        return self._spec.side_effects

    @property
    def timeout_s(self) -> float:
        return self._spec.timeout_s

    # Tool protocol method ------------------------------------------------

    async def invoke(
        self,
        args: dict[str, object],
        ctx: TurnContext,
    ) -> ToolResultBlock:
        """Validate args against the Pydantic model, run the user fn,
        wrap the return value into a ``ToolResultBlock``."""
        parsed = self._spec.args_model.model_validate(args)
        output = await self._fn(parsed, ctx)
        return _wrap_output(output)


def _wrap_output(output: str | list[ContentBlock]) -> ToolResultBlock:
    """Wrap a user-function return into a ToolResultBlock.

    ``tool_use_id`` is intentionally blank here — the runner fills it
    in when it threads the result back to the model. Tests that need
    the id can post-process the block.

    Uses ``model_validate`` so that Pydantic handles the union-variance
    issue between ``ContentBlock`` (the full domain union, used
    elsewhere) and the narrower union ``ToolResultBlock.content``
    accepts (no nested tool-results).
    """
    return ToolResultBlock.model_validate(
        {"tool_use_id": "", "content": output, "is_error": False}
    )


def _validate_tier_and_effects(
    risk_tier: int, side_effects: Literal["none", "read", "write"]
) -> None:
    if risk_tier not in _ALLOWED_RISK_TIERS:
        raise ValueError(
            f"risk_tier must be one of {sorted(_ALLOWED_RISK_TIERS)}, "
            f"got {risk_tier}. Tier 5+ (code execution) is not supported in V1."
        )
    # Tier 0-2 must be read-only (none or read).
    if risk_tier in (0, 1, 2) and side_effects not in _READER_SIDE_EFFECTS:
        raise ValueError(
            f"risk_tier={risk_tier} implies a read-only tool "
            f"(side_effects in {sorted(_READER_SIDE_EFFECTS)}), "
            f"got side_effects={side_effects!r}. Tier 3+ is for mutating tools."
        )
    # Tier 3+ must not claim side_effects="none".
    if risk_tier >= 3 and side_effects == "none":
        raise ValueError(
            f"risk_tier={risk_tier} implies a mutating or external tool; "
            f'use side_effects="read" or "write" rather than "none".'
        )


def tool(
    *,
    name: str,
    description: str,
    risk_tier: int,
    side_effects: Literal["none", "read", "write"],
    timeout_s: float,
    args_model: type[BaseModel],
    approval_required: bool | None = None,
    tool_kind: Literal["native", "mcp", "delegation"] = "native",
) -> Callable[
    [Callable[..., Awaitable[str | list[ContentBlock]]]],
    _DecoratedTool,
]:
    """Decorator that turns an async callable into a ``Tool``.

    ``approval_required`` defaults to ``True`` for risk_tier >= 3 and
    ``False`` otherwise. Callers can override for specific cases (e.g.
    a tier-2 tool with an escalating side-effect).
    """
    _validate_tier_and_effects(risk_tier, side_effects)
    resolved_approval = approval_required if approval_required is not None else risk_tier >= 3
    spec = _ToolSpec(
        name=name,
        description=description,
        risk_tier=risk_tier,
        side_effects=side_effects,
        timeout_s=timeout_s,
        approval_required=resolved_approval,
        tool_kind=tool_kind,
        args_model=args_model,
        input_schema=args_model.model_json_schema(),
    )

    def decorator(
        fn: Callable[..., Awaitable[str | list[ContentBlock]]],
    ) -> _DecoratedTool:
        return _DecoratedTool(spec=spec, fn=fn)

    return decorator


# Re-export a helper for tools that need to produce rich content directly.
__all__ = ["TextBlock", "tool"]
