"""Cairn's Textual UI layer.

Entry points exported here stay narrow; internal widgets live under
underscore-prefixed submodules.
"""

from __future__ import annotations

from cairn.ui._app import CairnApp
from cairn.ui._gateway import TextualApprovalGateway
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._screens import ApprovalModal, SessionScreen

__all__ = [
    # App
    "CairnApp",
    # Gateway
    "TextualApprovalGateway",
    # Observer
    "TextualUIEventObserver",
    # Screens
    "ApprovalModal",
    "SessionScreen",
]
