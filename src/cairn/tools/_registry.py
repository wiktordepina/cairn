"""`DefaultToolRegistry` — session-type-scoped tool discovery.

Replaces the orchestrator's `EmptyToolRegistry` stub. Scoping rules
match security doc §11 lines 1057-1064:

- `EPHEMERAL` — `[]` unless `ephemeral_allowlist` names tools by
  name.
- `PERSONA` — names listed under `persona_allowlists[session.persona]`.
- `COMPANION` — the full set: `companion_tools + mcp_tools`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cairn.domain._enums import SessionType
from cairn.domain._provider import ToolDefinition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cairn.domain import Session
    from cairn.orchestrator._protocols import Tool


class DefaultToolRegistry:
    """Session-type-scoped tool registry.

    Construct with the full lists at the harness-assembly layer (CLI);
    the orchestrator asks `for_session` per turn.
    """

    def __init__(
        self,
        *,
        companion_tools: Sequence[Tool] = (),
        persona_allowlists: dict[str, list[str]] | None = None,
        ephemeral_allowlist: Sequence[str] = (),
        mcp_tools: Sequence[Tool] = (),
    ) -> None:
        self._companion: list[Tool] = list(companion_tools)
        self._mcp: list[Tool] = list(mcp_tools)
        self._persona_allowlists: dict[str, list[str]] = dict(persona_allowlists or {})
        self._ephemeral_allowlist: frozenset[str] = frozenset(ephemeral_allowlist)

        # Unified name → Tool map for get(). Duplicates across the
        # companion and mcp lists are a config error.
        self._by_name: dict[str, Tool] = {}
        for t in (*self._companion, *self._mcp):
            if t.name in self._by_name:
                raise ValueError(
                    f"Duplicate tool name: {t.name!r}. Each tool must have a "
                    f"unique name across companion + MCP sets."
                )
            self._by_name[t.name] = t

    def for_session(self, session: Session) -> list[ToolDefinition]:
        """Return the ToolDefinitions visible to this session."""
        visible = self._visible_tools(session)
        return [
            ToolDefinition(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
            )
            for t in visible
        ]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def names(self) -> frozenset[str]:
        """Return all registered tool names (companion + mcp).

        Useful for bootstrap-time validation of configuration that
        references tool names (e.g. `tools.timeout_s_overrides`).
        """
        return frozenset(self._by_name.keys())

    # -- Internals -------------------------------------------------------

    def _visible_tools(self, session: Session) -> list[Tool]:
        match session.type:
            case SessionType.COMPANION:
                return [*self._companion, *self._mcp]
            case SessionType.PERSONA:
                allowed = self._persona_allowlists.get(session.persona, [])
                return [t for t in (*self._companion, *self._mcp) if t.name in allowed]
            case SessionType.EPHEMERAL:
                if not self._ephemeral_allowlist:
                    return []
                return [
                    t
                    for t in (*self._companion, *self._mcp)
                    if t.name in self._ephemeral_allowlist
                ]
        # Exhaustive SessionType match above; this placates pyright.
        return []  # pragma: no cover
