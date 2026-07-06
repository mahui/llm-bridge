"""Claude Code adapter - CLI subprocess mode."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from llm_bridge.convert.anthropic import format_messages_as_prompt
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

# Model name mapping: short name -> CLI model flag
MODEL_MAP = {
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-6": "opus",
    "sonnet": "sonnet",
    "opus": "opus",
}

AVAILABLE_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6"]

# Limit concurrent CLI processes
MAX_CONCURRENT = 2


class ClaudeProvider(BaseProvider):
    """Provider adapter using Claude CLI's --print mode."""

    def __init__(self, cli_path: str = "claude") -> None:
        super().__init__()
        self.cli_path = cli_path
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    @property
    def name(self) -> str:
        return "claude"

    async def initialize(self) -> None:
        # Verify claude CLI is available
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
            logger.info("Claude CLI found: %s", version)
            self._status = ProviderStatus.READY
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            logger.warning("Claude CLI not available: %s", e)
            self._status = ProviderStatus.ERROR

    async def shutdown(self) -> None:
        pass

    def _resolve_model(self, model: str) -> str:
        """Resolve model name to CLI flag."""
        model_key = model.split("/")[-1]
        return MODEL_MAP.get(model_key, "sonnet")

    async def _run_cli(
        self, prompt: str, model: str
    ) -> asyncio.subprocess.Process:
        """Start a Claude CLI subprocess. Prompt is sent via stdin."""
        args = [
            self.cli_path,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--model", self._resolve_model(model),
            "-p", "-",  # read prompt from stdin
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc.stdin.write(prompt.encode())
        proc.stdin.close()
        return proc

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        prompt = format_messages_as_prompt(request.messages)
        model_name = f"claude/{request.model.split('/', 1)[-1]}"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model)

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise ProviderError(
                    "Claude CLI timed out",
                    status_code=504,
                    retryable=True,
                    provider=self.name,
                )

        if proc.returncode != 0:
            raise ProviderError(
                f"Claude CLI exited with code {proc.returncode}: {stderr.decode()[:500]}",
                status_code=500,
                retryable=True,
                provider=self.name,
            )

        # Parse stream-json output: collect all text content
        text_parts = []
        usage = UsageInfo()

        for line in stdout.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "assistant":
                message = event.get("message", {})
                for block in message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                msg_usage = message.get("usage", {})
                usage.prompt_tokens += msg_usage.get("input_tokens", 0)
                usage.completion_tokens += msg_usage.get("output_tokens", 0)
            elif event.get("type") == "result":
                result_usage = event.get("usage", {})
                if isinstance(result_usage, dict) and result_usage:
                    usage.prompt_tokens = result_usage.get("input_tokens", usage.prompt_tokens)
                    usage.completion_tokens = result_usage.get("output_tokens", usage.completion_tokens)

        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        return ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(content="".join(text_parts)),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        prompt = format_messages_as_prompt(request.messages)
        model_name = f"claude/{request.model.split('/', 1)[-1]}"
        state = StreamState(model=model_name)

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model)

            yield make_role_chunk(state)

            try:
                async for raw_line in proc.stdout:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") == "assistant":
                        message = event.get("message", {})
                        for block in message.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                yield make_content_chunk(block["text"], state)
                    elif event.get("type") == "result":
                        break

                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()

        yield make_final_chunk(state)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=f"claude/{m}", owned_by="claude")
            for m in AVAILABLE_MODELS
        ]
