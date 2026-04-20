"""Cairn orchestrator — the turn loop that ties providers, persistence,
memory, tools, context assembly, and the UI together."""

from cairn.orchestrator._clock import Clock, FrozenClock, SystemClock
from cairn.orchestrator._config import OrchestratorConfig
from cairn.orchestrator._context import TurnContext
from cairn.orchestrator._enums import ApprovalOutcome, BudgetVerdict, TurnState
from cairn.orchestrator._errors import (
    OrchestratorError,
    TurnAlreadyRunning,
    TurnBudgetExceeded,
    TurnTimeout,
    UnknownSession,
)
from cairn.orchestrator._middleware import (
    ApprovalDecision,
    ApprovalRequest,
    MessagePreparer,
    ResultTransformer,
    ToolApprover,
    UIEventObserver,
)
from cairn.orchestrator._protocols import (
    ApprovalGateway,
    ContextManager,
    CostTracker,
    ExtractionQueue,
    MemoryService,
    Tool,
    ToolRegistry,
    ToolRunner,
)
from cairn.orchestrator._records import ApprovalDecisionRecord, TurnRecord
from cairn.orchestrator._stubs import (
    AutoApproveGateway,
    DenyAllGateway,
    EmptyToolRegistry,
    MinimalContextManager,
    NullExtractionQueue,
    NullMemoryService,
    RaisingToolRunner,
)

__all__ = [
    # Clock
    "Clock",
    "FrozenClock",
    "SystemClock",
    # Config
    "OrchestratorConfig",
    # Context
    "TurnContext",
    # Enums
    "ApprovalOutcome",
    "BudgetVerdict",
    "TurnState",
    # Errors
    "OrchestratorError",
    "TurnAlreadyRunning",
    "TurnBudgetExceeded",
    "TurnTimeout",
    "UnknownSession",
    # Middleware
    "ApprovalDecision",
    "ApprovalRequest",
    "MessagePreparer",
    "ResultTransformer",
    "ToolApprover",
    "UIEventObserver",
    # Protocols
    "ApprovalGateway",
    "ContextManager",
    "CostTracker",
    "ExtractionQueue",
    "MemoryService",
    "Tool",
    "ToolRegistry",
    "ToolRunner",
    # Records
    "ApprovalDecisionRecord",
    "TurnRecord",
    # Stubs
    "AutoApproveGateway",
    "DenyAllGateway",
    "EmptyToolRegistry",
    "MinimalContextManager",
    "NullExtractionQueue",
    "NullMemoryService",
    "RaisingToolRunner",
]
