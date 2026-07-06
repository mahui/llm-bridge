"""Gemini adapter - CLI subprocess mode."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

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

AVAILABLE_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# Model name mapping: normalize to CLI model flag
MODEL_MAP = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
}

MAX_CONCURRENT = 2


def _format_prompt(request: ChatCompletionRequest) -> str:
    """Format OpenAI messages as a single prompt string for Gemini CLI."""
    parts = []
    for msg in request.messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if msg.role == "system":
            parts.append(f"[System Instructions]\n{content}")
        elif msg.role == "user":
            parts.append(content)
        elif msg.role == "assistant":
            parts.append(f"[Previous Assistant Response]\n{content}")
    return "\n\n".join(parts)


class GeminiProvider(BaseProvider):
    """Provider adapter using Gemini CLI subprocess."""

    def __init__(self, cli_path: str = "gemini") -> None:
        super().__init__()
        self.cli_path = cli_path
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    @property
    def name(self) -> str:
        return "gemini"

    async def initialize(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
            logger.info("Gemini CLI found: %s", version)
            self._status = ProviderStatus.READY
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            logger.warning("Gemini CLI not available: %s", e)
            self._status = ProviderStatus.ERROR

    async def shutdown(self) -> None:
        pass

    def _resolve_model(self, model: str) -> str:
        model_key = model.split("/")[-1]
        return MODEL_MAP.get(model_key, model_key)

    async def _run_cli(
        self, prompt: str, model: str, capture_stderr: bool
    ) -> asyncio.subprocess.Process:
        """Start Gemini CLI subprocess. Prompt via stdin to avoid arg length limits.

        The streaming path never drains stderr, so it must be DEVNULL there —
        a full pipe buffer would deadlock the child. communicate() drains it,
        so the non-streaming path can capture it for error classification.
        """
        args = [
            self.cli_path,
            "-p", "-",  # read prompt from stdin
            "--output-format", "stream-json",
            "--model", self._resolve_model(model),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
        )
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        return proc

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        prompt = _format_prompt(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model, capture_stderr=True)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ProviderError(
                    "Gemini CLI timed out", status_code=504,
                    retryable=True, provider=self.name,
                )

        if proc.returncode != 0:
            err_text = stderr.decode()[:500]
            if "quota" in err_text.lower() or "exhausted" in err_text.lower():
                raise ProviderError(
                    f"Gemini rate limited: {err_text}",
                    status_code=429, retryable=True, provider=self.name,
                )
            raise ProviderError(
                f"Gemini CLI exited with code {proc.returncode}: {err_text}",
                status_code=500, retryable=True, provider=self.name,
            )

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

            etype = event.get("type")
            if etype == "message" and event.get("role") == "model":
                content = event.get("content", "")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
            elif etype == "result":
                stats = event.get("stats", {})
                usage.prompt_tokens = stats.get("input_tokens", 0)
                usage.completion_tokens = stats.get("output_tokens", 0)
                usage.total_tokens = stats.get("total_tokens", 0)
                if event.get("status") == "error":
                    err_msg = event.get("error", {}).get("message", "Unknown error")
                    if "quota" in err_msg.lower() or "exhausted" in err_msg.lower():
                        raise ProviderError(
                            f"Gemini rate limited: {err_msg}",
                            status_code=429, retryable=True, provider=self.name,
                        )
                    raise ProviderError(
                        f"Gemini CLI error: {err_msg}",
                        status_code=500, retryable=True, provider=self.name,
                    )

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

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        prompt = _format_prompt(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"
        state = StreamState(model=model_name)
        finish_reason = "stop"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model, capture_stderr=False)
            try:
                yield make_role_chunk(state)

                async for raw_line in proc.stdout:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "message" and event.get("role") == "model":
                        content = event.get("content", "")
                        if isinstance(content, str) and content:
                            yield make_content_chunk(content, state)
                        elif isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    text = part.get("text", "")
                                    if text:
                                        yield make_content_chunk(text, state)
                    elif etype == "result":
                        if event.get("status") == "error":
                            err_msg = event.get("error", {}).get("message", "Unknown error")
                            raise ProviderError(
                                f"Gemini CLI error: {err_msg}",
                                status_code=429 if "quota" in err_msg.lower() else 500,
                                retryable=True, provider=self.name,
                            )
                        break

                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    finish_reason = "length"
            finally:
                # Runs on normal exit, ProviderError, and client disconnect
                # (GeneratorExit) alike: never leave an orphan CLI process.
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

        yield make_final_chunk(state, finish_reason=finish_reason)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=f"gemini/{m}", owned_by="gemini")
            for m in AVAILABLE_MODELS
        ]
