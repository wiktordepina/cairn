# Orchestrator — API reference

The public surface of the turn loop: the `Orchestrator` itself, the
collaborator protocols it expects, the typed middleware seams, the turn-state
records persisted per turn, and the runtime knobs.

For the narrative — state machine, cancellation, crash recovery — see
[Orchestrator](../orchestrator.md).

## Entry point

::: cairn.orchestrator.Orchestrator

::: cairn.orchestrator.SessionManager

## Config

::: cairn.orchestrator.OrchestratorConfig

## Collaborator protocols

::: cairn.orchestrator.ApprovalGateway

::: cairn.orchestrator.ContextManager

::: cairn.orchestrator.CostTracker

::: cairn.orchestrator.ExtractionQueue

::: cairn.orchestrator.MemoryService

::: cairn.orchestrator.Tool

::: cairn.orchestrator.ToolRegistry

::: cairn.orchestrator.ToolRunner

## Middleware

::: cairn.orchestrator.MessagePreparer

::: cairn.orchestrator.ResultTransformer

::: cairn.orchestrator.ToolApprover

::: cairn.orchestrator.UIEventObserver

::: cairn.orchestrator.ApprovalRequest

::: cairn.orchestrator.ApprovalDecision

## Turn context and records

::: cairn.orchestrator.TurnContext

::: cairn.orchestrator.TurnRecord

::: cairn.orchestrator.ApprovalDecisionRecord

## Enums

::: cairn.orchestrator.ApprovalOutcome

::: cairn.orchestrator.BudgetVerdict

::: cairn.orchestrator.TurnState

## Errors

::: cairn.orchestrator.OrchestratorError

::: cairn.orchestrator.TurnAlreadyRunning

::: cairn.orchestrator.TurnBudgetExceeded

::: cairn.orchestrator.TurnTimeout

::: cairn.orchestrator.UnknownSession

## Clock

::: cairn.orchestrator.Clock

::: cairn.orchestrator.SystemClock

::: cairn.orchestrator.FrozenClock

## Cost tracking

::: cairn.orchestrator.BasicCostTracker

## Stubs

Default no-op implementations the orchestrator ships with. Replace with real
implementations as each brick lands.

::: cairn.orchestrator.AutoApproveGateway

::: cairn.orchestrator.DenyAllGateway

::: cairn.orchestrator.EmptyToolRegistry

::: cairn.orchestrator.MinimalContextManager

::: cairn.orchestrator.NullExtractionQueue

::: cairn.orchestrator.NullMemoryService

::: cairn.orchestrator.RaisingToolRunner
