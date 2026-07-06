"""Codex adapter - CLI subprocess mode via `codex exec --json`."""

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
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex",
    "gpt-5.1",
    "gpt-5-codex",
    "gpt-5",
    "gpt-5-codex-mini",
]

MAX_CONCURRENT = 2


def _format_prompt(request: ChatCompletionRequest) -> str:
    """Format OpenAI messages as a single prompt string for Codex CLI."""
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


class CodexProvider(BaseProvider):
    """Provider adapter using Codex CLI subprocess (codex exec --json)."""

    def __init__(self, cli_path: str = "codex", ignore_user_config: bool = True) -> None:
        super().__init__()
        self.cli_path = cli_path
        # ~/.codex/config.toml pulls in the user's skills/plugins and reasoning
        # settings, which can add tens of thousands of input tokens per chat
        # request. Auth is unaffected by --ignore-user-config.
        self.ignore_user_config = ignore_user_config
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    @property
    def name(self) -> str:
        return "codex"

    async def initialize(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
            logger.info("Codex CLI found: %s", version)
            self._status = ProviderStatus.READY
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            logger.warning("Codex CLI not available: %s", e)
            self._status = ProviderStatus.ERROR

    async def shutdown(self) -> None:
        pass

    def _resolve_model(self, model: str) -> str:
        return model.split("/")[-1]

    async def _run_cli(
        self, prompt: str, model: str, capture_stderr: bool
    ) -> asyncio.subprocess.Process:
        """Start a Codex CLI subprocess in exec mode. Prompt via stdin.

        The streaming path never drains stderr, so it must be DEVNULL there —
        a full pipe buffer would deadlock the child. communicate() drains it,
        so the non-streaming path can capture it for error messages.
        """
        args = [
            self.cli_path, "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        if self.ignore_user_config:
            args.append("--ignore-user-config")
        args += [
            "-m", self._resolve_model(model),
            "-",  # read prompt from stdin
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
        model_name = f"codex/{request.model.split('/', 1)[-1]}"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model, capture_stderr=True)

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ProviderError(
                    "Codex CLI timed out", status_code=504,
                    retryable=True, provider=self.name,
                )

        if proc.returncode != 0:
            err_text = stderr.decode()[:500]
            raise ProviderError(
                f"Codex CLI exited with code {proc.returncode}: {err_text}",
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

            etype = event.get("type", "")

            if etype == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text_parts.append(item.get("text", ""))
            elif etype == "turn.completed":
                u = event.get("usage", {})
                usage.prompt_tokens = u.get("input_tokens", 0)
                usage.completion_tokens = u.get("output_tokens", 0)
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

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        prompt = _format_prompt(request)
        model_name = f"codex/{request.model.split('/', 1)[-1]}"
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

                    etype = event.get("type", "")

                    if etype == "item.completed":
                        item = event.get("item", {})
                        if item.get("type") == "agent_message":
                            text = item.get("text", "")
                            if text:
                                yield make_content_chunk(text, state)
                    elif etype == "turn.completed":
                        break

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
        return [
            ModelInfo(id=f"codex/{m}", owned_by="codex")
            for m in AVAILABLE_MODELS
        ]
