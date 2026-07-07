"""Claude adapter - official claude-agent-sdk.

Uses the Agent SDK instead of hand-rolled `claude --print` subprocesses:
the SDK owns process lifecycle (including cleanup on client disconnect),
emits token-level stream events, and is the Anthropic-sanctioned way to
consume subscription quota headlessly (Agent SDK credits).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator

import httpx
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

# Model name mapping: full name -> CLI alias (current lineup per the CLI's
# interactive /model picker: Fable 5, Opus 4.8, Sonnet 5, Haiku 4.5).
# Unknown names pass through unchanged — the CLI accepts full model names.
MODEL_MAP = {
    "claude-fable-5": "fable",
    "claude-opus-4-8": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5": "haiku",
    "fable": "fable",
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
}

# Fallback when no Anthropic API key is configured for dynamic listing.
# (No CLI list-models command exists: anthropics/claude-code#12612)
FALLBACK_MODELS = [
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-haiku-4-5",
]

MODELS_API_URL = "https://api.anthropic.com/v1/models"
MODELS_CACHE_TTL = 3600.0  # seconds

# Request value -> SDK EffortLevel (SDK supports low/medium/high/xhigh/max;
# "minimal" is OpenAI vocabulary with no SDK equivalent, map to low).
EFFORT_MAP = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}
# The SDK defaults to "high" — too deep (and too credit-hungry) for a chat
# gateway. Requests can override via the OpenAI reasoning_effort field.
DEFAULT_EFFORT = "medium"

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

    def __init__(self, cli_path: str = "claude", api_key: str = "") -> None:
        super().__init__()
        self.cli_path = cli_path  # kept for config compat; SDK bundles its own CLI
        # Optional: only used for the free Models API listing endpoint, never
        # for inference (inference goes through the subscription-authed SDK).
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._models_cache: list[str] | None = None
        self._models_fetched_at = 0.0

    @property
    def name(self) -> str:
        return "claude"

    async def _detect_auth(self) -> str | None:
        """Best-effort check that Claude Code has credentials on this machine.

        The SDK bundles its own CLI, so the binary always exists — what a
        fresh machine lacks is a login. Returns a human-readable source, or
        None when no known credential signal is present.
        """
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return "env:CLAUDE_CODE_OAUTH_TOKEN"
        creds = Path.home() / ".claude" / ".credentials.json"
        if creds.exists():
            return str(creds)
        claude_json = Path.home() / ".claude.json"
        try:
            if claude_json.exists() and "oauthAccount" in claude_json.read_text():
                return f"{claude_json} (oauthAccount)"
        except OSError:
            pass
        if sys.platform == "darwin":
            # macOS stores Claude Code OAuth credentials in the Keychain
            try:
                proc = await asyncio.create_subprocess_exec(
                    "security", "find-generic-password", "-s", "Claude Code-credentials",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if await asyncio.wait_for(proc.wait(), timeout=5) == 0:
                    return "macOS Keychain"
            except (OSError, asyncio.TimeoutError):
                pass
        return None

    async def initialize(self) -> None:
        from claude_agent_sdk._cli_version import __cli_version__

        auth_source = await self._detect_auth()
        if auth_source is None:
            logger.warning(
                "Claude provider: no credentials found — run `claude` and log in "
                "(or `claude setup-token`), then restart"
            )
            self._status = ProviderStatus.ERROR
            return
        logger.info(
            "Claude provider ready (claude-agent-sdk, bundled CLI %s, auth: %s)",
            __cli_version__, auth_source,
        )
        self._status = ProviderStatus.READY

    async def shutdown(self) -> None:
        pass

    def _build_options(
        self, request: ChatCompletionRequest, system_prompt: str | None, streaming: bool
    ) -> ClaudeAgentOptions:
        model_key = request.model.split("/")[-1]
        effort = EFFORT_MAP.get(request.reasoning_effort or "", DEFAULT_EFFORT)
        return ClaudeAgentOptions(
            model=MODEL_MAP.get(model_key, model_key),
            system_prompt=system_prompt,
            tools=[],  # pure chat: no built-in tools
            max_turns=1,
            include_partial_messages=streaming,
            effort=effort,
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

    async def _fetch_models_api(self) -> list[str] | None:
        """List models via the Anthropic Models API (free endpoint, no quota).

        Returns None when no API key is configured or the request fails —
        callers fall back to FALLBACK_MODELS.
        """
        if not self.api_key:
            return None
        if self._models_cache and time.monotonic() - self._models_fetched_at < MODELS_CACHE_TTL:
            return self._models_cache
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    MODELS_API_URL,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                resp.raise_for_status()
                ids = [
                    m["id"]
                    for m in resp.json().get("data", [])
                    if m.get("id", "").startswith("claude")
                ]
            if ids:
                self._models_cache = ids
                self._models_fetched_at = time.monotonic()
                return ids
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("Claude Models API listing failed, using fallback: %s", e)
        return None

    async def list_models(self) -> list[ModelInfo]:
        ids = await self._fetch_models_api() or FALLBACK_MODELS
        return [
            ModelInfo(id=f"claude/{m}", owned_by="claude")
            for m in ids
        ]
