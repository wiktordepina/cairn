# Domain — API reference

The core data types that flow through cairn: messages, content blocks,
sessions, memory entries, provider events, and the UI event stream.

For context on how these types compose into a turn, see
[Architecture](../architecture.md).

## Messages and content

::: cairn.domain.Message

::: cairn.domain.ContentBlock

::: cairn.domain.TextBlock

::: cairn.domain.ImageBlock

::: cairn.domain.ImageSource

::: cairn.domain.ThinkingBlock

::: cairn.domain.ToolUseBlock

::: cairn.domain.ToolResultBlock

## Sessions and memory

::: cairn.domain.Session

::: cairn.domain.MemoryEntry

## Provider events

Types produced by adapters in the provider layer and consumed by the
orchestrator.

::: cairn.domain.ProviderRequest

::: cairn.domain.ProviderEvent

::: cairn.domain.TextDelta

::: cairn.domain.ToolCallStart

::: cairn.domain.ToolCallDelta

::: cairn.domain.ToolCallEnd

::: cairn.domain.MessageStop

::: cairn.domain.UsageEvent

::: cairn.domain.ToolDefinition

## UI events

Emitted by the orchestrator for UI layers and observers.

::: cairn.domain.UIEvent

::: cairn.domain.AssistantMessageComplete

::: cairn.domain.AssistantTextDelta

::: cairn.domain.DelegationCompleted

::: cairn.domain.DelegationSpawned

::: cairn.domain.ObservationExtractionRequested

::: cairn.domain.SessionArchived

::: cairn.domain.SessionCreated

::: cairn.domain.SessionResumed

::: cairn.domain.ToolCallCompleted

::: cairn.domain.ToolCallStarted

::: cairn.domain.TurnComplete

::: cairn.domain.UserMessagePersisted

## Enums

::: cairn.domain.ErrorClass

::: cairn.domain.MemoryClass

::: cairn.domain.MemoryEntryType

::: cairn.domain.SessionType

::: cairn.domain.StopReason

::: cairn.domain.ToolCallStatus

::: cairn.domain.UsageOperation
