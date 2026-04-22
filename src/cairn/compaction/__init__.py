"""Conversation-history compaction — keeps the provider request under budget.

The V1 brick is truncation-only: drops whole turn blocks from the front
of `ProviderRequest.messages` until the request fits the compaction
budget, preserving the last N turns verbatim. When the preserve-floor
is hit and the request is still over budget, a
`BudgetOverflowGateway` decides whether to continue (user accepts the
risk) or terminate the session. See `.plan/compaction-brick-design.md`.
"""

from __future__ import annotations

from cairn.compaction._errors import BudgetOverflowDeclined
from cairn.compaction._gateway import (
    AutoContinueOverflowGateway,
    AutoTerminateOverflowGateway,
    BudgetOverflowGateway,
)
from cairn.compaction._preparer import TruncatingCompactor
from cairn.compaction._turns import TurnBlock, iter_turn_blocks
from cairn.config import CompactionConfig

__all__ = [
    # Preparer
    "TruncatingCompactor",
    # Config re-export
    "CompactionConfig",
    # Gateway protocol + stubs
    "BudgetOverflowGateway",
    "AutoContinueOverflowGateway",
    "AutoTerminateOverflowGateway",
    # Errors
    "BudgetOverflowDeclined",
    # Turn-block utilities
    "iter_turn_blocks",
    "TurnBlock",
]
