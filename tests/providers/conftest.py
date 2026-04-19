"""Shared fixtures for provider tests."""

from __future__ import annotations

import pytest

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import TextBlock, ToolResultBlock, ToolUseBlock
from cairn.domain._messages import Message
from cairn.domain._provider import ProviderRequest, ToolDefinition


@pytest.fixture
def secret_resolver() -> SecretResolver:
    """A resolver that returns a fixed API key for any env: ref."""
    resolver = SecretResolver()
    return resolver


@pytest.fixture
def anthropic_config() -> ProviderConfig:
    return ProviderConfig(name="anthropic", api_key=SecretRef.parse("literal:test-key"))


@pytest.fixture
def openai_config() -> ProviderConfig:
    return ProviderConfig(name="openai", api_key=SecretRef.parse("literal:test-key"))


@pytest.fixture
def openrouter_config() -> ProviderConfig:
    return ProviderConfig(
        name="openrouter",
        api_key=SecretRef.parse("literal:test-key"),
        base_url="https://openrouter.ai/api/v1",
    )


def make_simple_request(text: str = "Hello", model: str = "test-model") -> ProviderRequest:
    """Create a minimal ProviderRequest with a single user message."""
    msg = Message(role="user")
    msg.content.append(TextBlock(text=text))
    return ProviderRequest(model=model, messages=[msg])


def make_tool_request() -> ProviderRequest:
    """Create a ProviderRequest with tools defined."""
    msg = Message(role="user")
    msg.content.append(TextBlock(text="What's the weather?"))
    tool = ToolDefinition(
        name="get_weather",
        description="Get current weather",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )
    return ProviderRequest(model="test-model", messages=[msg], tools=[tool])


def make_conversation_with_tool_result() -> ProviderRequest:
    """Create a multi-turn conversation including a tool result."""
    user_msg = Message(role="user")
    user_msg.content.append(TextBlock(text="What's the weather in London?"))

    assistant_msg = Message(role="assistant")
    assistant_msg.content.append(
        ToolUseBlock(id="tc-1", name="get_weather", input={"city": "London"})
    )

    result_msg = Message(role="user")
    result_msg.content.append(ToolResultBlock(tool_use_id="tc-1", content="Sunny, 22°C"))

    return ProviderRequest(
        model="test-model",
        messages=[user_msg, assistant_msg, result_msg],
    )
