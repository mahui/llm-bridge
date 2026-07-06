"""Claude adapter - official claude-agent-sdk.

Uses the Agent SDK instead of hand-rolled `claude --print` subprocesses:
the SDK owns process lifecycle (including cleanup on client disconnect),
emits token-level stream events, and is the Anthropic-sanctioned way to
consume subscription quota headlessly (Agent SDK credits).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    StreamEvent,
    TextBlock,
    query,
)

from llm_bridge.convert.streaming import (
    StreamState,
    make_content_chunk,
    make_final_chunk,
    make_role_chunk,
)
from llm_bridge.models import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
    UsageInfo,
)
from llm_bridge.providers.base import BaseProvider, ProviderError, ProviderStatus

logger = logging.getLogger(__name__)

# Model name mapping: short name -> SDK model alias
MODEL_MAP = {
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-6": "opus",
    "sonnet": "sonnet",
    "opus": "opus",
}

AVAILABLE_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6"]

# Limit concurrent SDK sessions (each spawns a CLI process)
MAX_CONCURRENT = 2

STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


def _split_messages(request: ChatCompletionRequest) -> tuple[str | None, str]:
    """Split OpenAI messages into (system_prompt, flattened_prompt)."""
    system_parts = []
    parts = []
    for msg in request.messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if msg.role == "system":
            system_parts.append(content)
        elif msg.role == "user":
            parts.append(content)
        elif msg.role == "assistant":
            parts.append(f"[Previous Assistant Response]\n{content}")
    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, "\n\n".join(parts)


class ClaudeProvider(BaseProvider):
    """Provider adapter using the official claude-agent-sdk."""

    def __init__(self, cli_path: str = "claude") -> None:
        super().__init__()
        self.cli_path = cli_path  # kept for config compat; SDK bundles its own CLI
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    @property
    def name(self) -> str:
        return "claude"

    async def initialize(self) -> None:
        # The SDK bundles its own CLI, so availability is not PATH-dependent.
        # Auth problems surface per-request as ProviderError.
        from claude_agent_sdk._cli_version import __cli_version__

        logger.info("Claude provider using claude-agent-sdk (bundled CLI %s)", __cli_version__)
        self._status = ProviderStatus.READY

    async def shutdown(self) -> None:
        pass

    def _build_options(
        self, request: ChatCompletionRequest, system_prompt: str | None, streaming: bool
    ) -> ClaudeAgentOptions:
        model_key = request.model.split("/")[-1]
        return ClaudeAgentOptions(
            model=MODEL_MAP.get(model_key, "sonnet"),
            system_prompt=system_prompt,
            tools=[],  # pure chat: no built-in tools
            max_turns=1,
            include_partial_messages=streaming,
        )

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        system_prompt, prompt = _split_messages(request)
        model_name = f"claude/{request.model.split('/', 1)[-1]}"
        options = self._build_options(request, system_prompt, streaming=False)

        text_parts: list[str] = []
        usage = UsageInfo()
        finish_reason = "stop"

        async with self._semaphore:
            try:
                async with aclosing(query(prompt=prompt, options=options)) as messages:
                    async for message in messages:
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    text_parts.append(block.text)
                            if message.stop_reason:
                                finish_reason = STOP_REASON_MAP.get(message.stop_reason, "stop")
                        elif isinstance(message, ResultMessage):
                            if message.is_error:
                                raise ProviderError(
                                    f"Claude SDK error: {message.result or message.subtype}",
                                    status_code=500, retryable=True, provider=self.name,
                                )
                            if message.usage:
                                usage.prompt_tokens = message.usage.get("input_tokens", 0)
                                usage.completion_tokens = message.usage.get("output_tokens", 0)
            except ClaudeSDKError as e:
                raise ProviderError(
                    f"Claude SDK error: {e}", status_code=500,
                    retryable=True, provider=self.name,
                ) from e

        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        return ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(content="".join(text_parts)),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        system_prompt, prompt = _split_messages(request)
        model_name = f"claude/{request.model.split('/', 1)[-1]}"
        options = self._build_options(request, system_prompt, streaming=True)
        state = StreamState(model=model_name)
        usage: UsageInfo | None = None
        finish_reason = "stop"

        async with self._semaphore:
            yield make_role_chunk(state)
            try:
                # aclosing() propagates generator close (client disconnect)
                # into the SDK, which tears down its CLI process.
                async with aclosing(query(prompt=prompt, options=options)) as messages:
                    async for message in messages:
                        if isinstance(message, StreamEvent):
                            event = message.event
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta" and delta.get("text"):
                                    yield make_content_chunk(delta["text"], state)
                            elif event.get("type") == "message_delta":
                                stop = event.get("delta", {}).get("stop_reason")
                                if stop:
                                    finish_reason = STOP_REASON_MAP.get(stop, "stop")
                        elif isinstance(message, ResultMessage):
                            if message.is_error:
                                raise ProviderError(
                                    f"Claude SDK error: {message.result or message.subtype}",
                                    status_code=500, retryable=True, provider=self.name,
                                )
                            if message.usage:
                                usage = UsageInfo(
                                    prompt_tokens=message.usage.get("input_tokens", 0),
                                    completion_tokens=message.usage.get("output_tokens", 0),
                                    total_tokens=message.usage.get("input_tokens", 0)
                                    + message.usage.get("output_tokens", 0),
                                )
            except ClaudeSDKError as e:
                raise ProviderError(
                    f"Claude SDK error: {e}", status_code=500,
                    retryable=True, provider=self.name,
                ) from e

        yield make_final_chunk(state, finish_reason=finish_reason, usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=f"claude/{m}", owned_by="claude")
            for m in AVAILABLE_MODELS
        ]
