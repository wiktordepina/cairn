"""Session-type palette + theme helpers.

Per arch-doc §4.14: "Subtle colour accent (companion teal, persona
amber, ephemeral grey) plus a badge." Palette values are exposed as
plain hex strings so widgets can compose them into Textual rich
styles without a hard dep on the config layer.
"""

from __future__ import annotations

from cairn.domain._enums import SessionType

# Default accent per session type. Overridable via
# `ProfileConfig.ui.session_type_colours` in a later PR; Tranche 1
# uses the defaults directly.
DEFAULT_SESSION_TYPE_COLOURS: dict[SessionType, str] = {
    SessionType.COMPANION: "#3bb0a8",  # teal
    SessionType.PERSONA: "#e0a030",  # amber
    SessionType.EPHEMERAL: "#808080",  # grey
}


def colour_for(
    session_type: SessionType,
    *,
    overrides: dict[SessionType, str] | None = None,
) -> str:
    """Return the hex colour for a session type.

    Args:
        session_type: The session type to look up.
        overrides: Optional user-supplied overrides from config.

    Returns:
        Hex colour string (e.g. ``"#3bb0a8"``).
    """
    if overrides and session_type in overrides:
        return overrides[session_type]
    return DEFAULT_SESSION_TYPE_COLOURS[session_type]
