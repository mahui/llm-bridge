"""Antigravity CLI (agy) adapter - CLI subprocess mode.

Successor to the retired Gemini CLI provider. The Antigravity CLI is
Google's current harness and exposes Gemini, Claude (thinking), and
GPT-OSS models under one subscription login. Reasoning depth is encoded
in the model variants themselves ("... (Low/Medium/High)"), so the
OpenAI reasoning_effort field is ignored here.
"""

from __future__ import annotations

import asyncio
import logging
import re
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

# Fallback when `agy models` fails; display names as the CLI shows them.
FALLBACK_MODELS = [
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]

MAX_CONCURRENT = 2


def _slugify(display_name: str) -> str:
    """'Claude Sonnet 4.6 (Thinking)' -> 'claude-sonnet-4.6-thinking'."""
    s = display_name.lower().replace("(", "").replace(")", "")
    return re.sub(r"\s+", "-", s.strip())


def _format_prompt(request: ChatCompletionRequest) -> str:
    """Format OpenAI messages as a single prompt string for the agy CLI."""
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


class AgyProvider(BaseProvider):
    """Provider adapter using the Antigravity CLI (`agy`) subprocess."""

    def __init__(self, cli_path: str = "agy") -> None:
        super().__init__()
        self.cli_path = cli_path
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._model_map: dict[str, str] = {}  # slug -> CLI display name

    @property
    def name(self) -> str:
        return "agy"

    async def initialize(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
            logger.info("Antigravity CLI found: %s", version)
            self._status = ProviderStatus.READY
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            logger.warning("Antigravity CLI not available: %s", e)
            self._status = ProviderStatus.ERROR
            return

        await self._load_models()

    async def _load_models(self) -> None:
        """Build slug -> display-name map from `agy models` (dynamic list)."""
        names = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                names = [line.strip() for line in stdout.decode().splitlines() if line.strip()]
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning("agy models failed, using fallback list: %s", e)
        self._model_map = {_slugify(n): n for n in (names or FALLBACK_MODELS)}
        logger.info("Antigravity models loaded: %d", len(self._model_map))

    async def shutdown(self) -> None:
        pass

    def _resolve_model(self, model: str) -> str:
        """Map a slug back to the CLI display name; pass unknowns through."""
        model_key = model.split("/")[-1]
        return self._model_map.get(model_key, model_key)

    async def _run_cli(
        self, prompt: str, model: str, capture_stderr: bool
    ) -> asyncio.subprocess.Process:
        """Start an agy subprocess. Prompt via stdin to avoid arg length limits.

        The streaming path never drains stderr, so it must be DEVNULL there —
        a full pipe buffer would deadlock the child. communicate() drains it,
        so the non-streaming path can capture it for error messages.
        """
        args = [
            self.cli_path,
            "-p", "-",  # read prompt from stdin
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
        model_name = f"agy/{request.model.split('/', 1)[-1]}"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model, capture_stderr=True)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ProviderError(
                    "Antigravity CLI timed out", status_code=504,
                    retryable=True, provider=self.name,
                )

        if proc.returncode != 0:
            err_text = stderr.decode()[:500]
            if "quota" in err_text.lower() or "rate" in err_text.lower():
                raise ProviderError(
                    f"Antigravity rate limited: {err_text}",
                    status_code=429, retryable=True, provider=self.name,
                )
            raise ProviderError(
                f"Antigravity CLI exited with code {proc.returncode}: {err_text}",
                status_code=500, retryable=True, provider=self.name,
            )

        return ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(content=stdout.decode().strip()),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(),  # plain-text output: no token accounting
        )

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        prompt = _format_prompt(request)
        model_name = f"agy/{request.model.split('/', 1)[-1]}"
        state = StreamState(model=model_name)
        finish_reason = "stop"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model, capture_stderr=False)
            try:
                yield make_role_chunk(state)

                # Plain-text output: forward stdout chunks as they arrive
                # (chunk-level, not token-level — the CLI has no event stream).
                while True:
                    data = await proc.stdout.read(4096)
                    if not data:
                        break
                    text = data.decode(errors="replace")
                    if text:
                        yield make_content_chunk(text, state)

                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    finish_reason = "length"
            finally:
                # Runs on normal exit, errors, and client disconnect
                # (GeneratorExit) alike: never leave an orphan CLI process.
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

        yield make_final_chunk(state, finish_reason=finish_reason)

    async def list_models(self) -> list[ModelInfo]:
        slugs = list(self._model_map.keys()) or [_slugify(n) for n in FALLBACK_MODELS]
        return [ModelInfo(id=f"agy/{s}", owned_by="agy") for s in slugs]
