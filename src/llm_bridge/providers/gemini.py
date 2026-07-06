"""Gemini adapter - CLI subprocess (primary) + Cloud Code Assist API (fallback)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from llm_bridge.auth.manager import AuthManager
from llm_bridge.convert.gemini import from_gemini_response, to_gemini_request
from llm_bridge.convert.streaming import (
    StreamState,
    make_content_chunk,
    make_final_chunk,
    make_role_chunk,
    parse_sse,
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
    """Provider adapter using Gemini CLI subprocess (primary) with API fallback."""

    def __init__(
        self,
        auth_manager: AuthManager,
        cli_path: str = "gemini",
        api_base: str = "https://cloudcode-pa.googleapis.com",
        project_id: str = "",
        mode: str = "cli",  # "cli" or "api"
    ) -> None:
        super().__init__()
        self.auth_manager = auth_manager
        self.cli_path = cli_path
        self.api_base = api_base.rstrip("/")
        self.project_id = project_id
        self.mode = mode
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._cli_available = False

    @property
    def name(self) -> str:
        return "gemini"

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), http2=True)

        # Check if Gemini CLI is available
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
            self._cli_available = True
            logger.info("Gemini CLI found: %s", version)
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            logger.warning("Gemini CLI not available: %s", e)
            self._cli_available = False

        if self._cli_available and self.mode == "cli":
            self._status = ProviderStatus.READY
            logger.info("Gemini provider initialized (mode: cli)")
            return

        # Fallback to API mode
        if not self.auth_manager.is_authenticated("gemini"):
            if self._cli_available:
                self._status = ProviderStatus.READY
                self.mode = "cli"
                logger.info("Gemini provider initialized (mode: cli, no API creds)")
            else:
                self._status = ProviderStatus.ERROR
                logger.warning("Gemini: no CLI and no API credentials")
            return

        # API mode: get project_id
        try:
            if not self.project_id:
                await self._load_code_assist()
            self.mode = "api"
            self._status = ProviderStatus.READY
            logger.info("Gemini provider initialized (mode: api, project: %s)", self.project_id)
        except Exception as e:
            if self._cli_available:
                self.mode = "cli"
                self._status = ProviderStatus.READY
                logger.info("Gemini API init failed, using CLI mode: %s", e)
            else:
                logger.warning("Gemini initialization failed: %s", e)
                self._status = ProviderStatus.ERROR

    async def _load_code_assist(self) -> None:
        """Call loadCodeAssist to get the server-assigned project ID."""
        headers = await self._get_headers()
        resp = await self._client.post(
            f"{self.api_base}/v1internal:loadCodeAssist",
            headers=headers,
            json={},
        )
        if resp.status_code == 200:
            data = resp.json()
            project = data.get("cloudaicompanionProject", "")
            if project:
                self.project_id = project
                logger.info("Gemini: obtained project_id=%s", project)
        else:
            logger.warning("Gemini loadCodeAssist returned %d", resp.status_code)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # CLI mode
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str) -> str:
        model_key = model.split("/")[-1]
        return MODEL_MAP.get(model_key, model_key)

    async def _run_cli(self, prompt: str, model: str) -> asyncio.subprocess.Process:
        """Start Gemini CLI subprocess. Prompt via stdin to avoid arg length limits."""
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
            stderr=asyncio.subprocess.PIPE,
        )
        proc.stdin.write(prompt.encode())
        proc.stdin.close()
        return proc

    async def _complete_cli(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        prompt = _format_prompt(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"

        async with self._semaphore:
            proc = await self._run_cli(prompt, request.model)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                raise ProviderError(
                    "Gemini CLI timed out", status_code=504,
                    retryable=True, provider=self.name,
                )

        if proc.returncode != 0:
            err_text = stderr.decode()[:500]
            # Check for rate limiting in CLI error
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
                # Check for error in result
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

    async def _stream_cli(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        prompt = _format_prompt(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"
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

                await asyncio.wait_for(proc.wait(), timeout=10)
            except ProviderError:
                proc.kill()
                raise
            except asyncio.TimeoutError:
                proc.kill()

        yield make_final_chunk(state)

    # ------------------------------------------------------------------
    # API mode
    # ------------------------------------------------------------------

    async def _get_headers(self) -> dict[str, str]:
        token = await self.auth_manager.get_access_token("gemini")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "llm-bridge/0.1.0 (compatible; GeminiCLI)",
            "X-Goog-Api-Client": "gl-python/3.12",
        }

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        gemini_body = to_gemini_request(request)
        model_key = request.model.split("/")[-1]
        payload: dict = {
            "model": model_key,
            "request": gemini_body,
            "userAgent": "GeminiCLI",
        }
        if self.project_id:
            payload["project"] = self.project_id
        return payload

    async def _complete_api(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        headers = await self._get_headers()
        payload = self._build_payload(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"

        resp = await self._client.post(
            f"{self.api_base}/v1internal:generateContent",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            raise ProviderError(
                f"Gemini API error: {resp.status_code} {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=resp.status_code in (429, 500, 502, 503),
                provider=self.name,
            )
        return from_gemini_response(resp.json(), model=model_name)

    async def _stream_api(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        headers = await self._get_headers()
        payload = self._build_payload(request)
        model_name = f"gemini/{request.model.split('/', 1)[-1]}"
        state = StreamState(model=model_name)

        url = f"{self.api_base}/v1internal:streamGenerateContent?alt=sse"

        async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise ProviderError(
                    f"Gemini stream error: {resp.status_code} {body.decode()[:500]}",
                    status_code=resp.status_code,
                    retryable=resp.status_code in (429, 500, 502, 503),
                    provider=self.name,
                )

            yield make_role_chunk(state)

            async for event in parse_sse(resp.aiter_lines()):
                if event.data == "[DONE]":
                    break
                try:
                    data = json.loads(event.data)
                except json.JSONDecodeError:
                    continue

                candidates = data.get("candidates", [])
                for candidate in candidates:
                    content = candidate.get("content", {})
                    for part in content.get("parts", []):
                        text = part.get("text")
                        if text:
                            yield make_content_chunk(text, state)

        yield make_final_chunk(state)

    # ------------------------------------------------------------------
    # Public interface (routes to CLI or API based on mode)
    # ------------------------------------------------------------------

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        if self.mode == "cli":
            return await self._complete_cli(request)
        return await self._complete_api(request)

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if self.mode == "cli":
            async for chunk in self._stream_cli(request):
                yield chunk
        else:
            async for chunk in self._stream_api(request):
                yield chunk

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=f"gemini/{m}", owned_by="gemini")
            for m in AVAILABLE_MODELS
        ]
