"""Session-type palette + theme helpers.

Per arch-doc §4.14: "Subtle colour accent (companion teal, persona
amber, ephemeral grey) plus a badge." Palette values are exposed as
plain hex strings so widgets can compose them into Textual rich
styles without a hard dep on the config layer.
"""

from __future__ import annotations

from cairn.domain._enums import SessionType

# Default accent per session type. Overridable via
# `ProfileConfig.ui.session_type_colours` (keyed by the string form
# of the session type, e.g. ``{"companion": "#ff0000"}``).
DEFAULT_SESSION_TYPE_COLOURS: dict[SessionType, str] = {
    SessionType.COMPANION: "#3bb0a8",  # teal
    SessionType.PERSONA: "#e0a030",  # amber
    SessionType.EPHEMERAL: "#808080",  # grey
}


def colour_for(
    session_type: SessionType,
    *,
    overrides: dict[SessionType, str] | dict[str, str] | None = None,
) -> str:
    """Return the hex colour for a session type.

    Args:
        session_type: The session type to look up.
        overrides: Optional user-supplied overrides. Keys may be the
            `SessionType` enum value or the string form; `SessionType`
            is a `StrEnum` so the two are interchangeable. Accepting
            both lets callers pass `UIConfig.session_type_colours`
            directly without enum coercion.

    Returns:
        Hex colour string (e.g. ``"#3bb0a8"``).
    """
    if overrides:
        merged: dict[object, str] = dict(overrides)  # type: ignore[arg-type]
        if session_type in merged:
            return merged[session_type]
        if session_type.value in merged:
            return merged[session_type.value]
    return DEFAULT_SESSION_TYPE_COLOURS[session_type]
