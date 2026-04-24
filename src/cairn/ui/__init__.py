"""Cairn's Textual UI layer.

Entry points exported here stay narrow; internal widgets live under
underscore-prefixed submodules.
"""

from __future__ import annotations

from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._screens import SessionScreen

__all__ = [
    # App
    "CairnApp",
    # Screens
    "SessionScreen",
    # Observer
    "TextualUIEventObserver",
]
