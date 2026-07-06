"""Antigravity adapter - direct HTTP to Cloud Code Assist API."""

from __future__ import annotations

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
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
)
from llm_bridge.providers.base import BaseProvider, ProviderError, ProviderStatus

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6",
    "claude-sonnet-4-6-thinking",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
]


class AntigravityProvider(BaseProvider):
    """Provider adapter for Google Cloud Code Assist (Antigravity) API."""

    def __init__(
        self,
        auth_manager: AuthManager,
        api_base: str = "https://cloudcode-pa.googleapis.com",
        project_id: str = "",
    ) -> None:
        super().__init__()
        self.auth_manager = auth_manager
        self.api_base = api_base.rstrip("/")
        self.project_id = project_id
        self._client: httpx.AsyncClient | None = None
        self._fetched_models: list[str] = []

    @property
    def name(self) -> str:
        return "antigravity"

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), http2=True)
        try:
            # Load project ID via loadCodeAssist if not configured
            if not self.project_id:
                await self._load_code_assist()
            # Try to fetch available models to verify access
            await self._fetch_available_models()
            self._status = ProviderStatus.READY
            logger.info(
                "Antigravity provider initialized (project: %s, models: %d)",
                self.project_id, len(self._fetched_models),
            )
        except Exception as e:
            logger.warning("Antigravity initialization failed: %s", e)
            self._status = ProviderStatus.ERROR

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def _get_headers(self) -> dict[str, str]:
        token = await self.auth_manager.get_access_token("antigravity")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
            "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        }

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
                logger.info("Antigravity: obtained project_id=%s", project)
        else:
            logger.warning("loadCodeAssist returned %d: %s", resp.status_code, resp.text[:200])

    async def _fetch_available_models(self) -> list[str]:
        """Fetch available models from the API."""
        headers = await self._get_headers()
        resp = await self._client.post(
            f"{self.api_base}/v1internal:fetchAvailableModels",
            headers=headers,
            json={"project": self.project_id} if self.project_id else {},
        )
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            self._fetched_models = [m.get("name", m.get("id", "")) for m in models]
        return self._fetched_models

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        """Build the Cloud Code Assist request payload."""
        gemini_body = to_gemini_request(request)

        payload: dict = {
            "model": request.model.split("/", 1)[-1],  # strip provider prefix
            "request": gemini_body,
            "userAgent": "antigravity",
        }
        if self.project_id:
            payload["project"] = self.project_id

        return payload

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        headers = await self._get_headers()
        payload = self._build_payload(request)

        resp = await self._client.post(
            f"{self.api_base}/v1internal:generateContent",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            raise ProviderError(
                f"Antigravity API error: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
                retryable=resp.status_code in (429, 500, 502, 503),
                provider=self.name,
            )
        return from_gemini_response(resp.json(), model=f"antigravity/{request.model.split('/', 1)[-1]}")

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        headers = await self._get_headers()
        payload = self._build_payload(request)
        model_name = f"antigravity/{request.model.split('/', 1)[-1]}"
        state = StreamState(model=model_name)

        url = f"{self.api_base}/v1internal:streamGenerateContent?alt=sse"

        async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise ProviderError(
                    f"Antigravity stream error: {resp.status_code} {body.decode()}",
                    status_code=resp.status_code,
                    retryable=resp.status_code in (429, 500, 502, 503),
                    provider=self.name,
                )

            if not state.sent_role:
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

    async def list_models(self) -> list[ModelInfo]:
        models = self._fetched_models or AVAILABLE_MODELS
        return [
            ModelInfo(id=f"antigravity/{m}", owned_by="antigravity")
            for m in models
        ]
