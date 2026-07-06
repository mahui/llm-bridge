"""Model routing with provider/model parsing and fallback chains."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from llm_bridge.config import BridgeConfig
from llm_bridge.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
)
from llm_bridge.providers import get_all_providers, get_provider
from llm_bridge.providers.base import BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class ModelRouter:
    """Routes model requests to the appropriate provider."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.aliases = config.routing.aliases
        self.default_model = config.routing.default_model

    def resolve(self, model: str) -> tuple[BaseProvider, str]:
        """Resolve a model reference to (provider, native_model_name).

        Model format: "provider/model-name" or alias.
        """
        # Check aliases first
        if model in self.aliases:
            model = self.aliases[model]

        # Parse provider/model
        if "/" in model:
            provider_name, model_name = model.split("/", 1)
        else:
            # No provider prefix: use default
            default = self.default_model
            if "/" in default:
                provider_name = default.split("/", 1)[0]
            else:
                provider_name = "claude"
            model_name = model

        provider = get_provider(provider_name)
        if provider is None:
            raise ProviderError(
                f"Provider '{provider_name}' not found or not initialized",
                status_code=404,
                provider=provider_name,
            )
        if not provider.is_healthy():
            raise ProviderError(
                f"Provider '{provider_name}' is not healthy (status: {provider.status.value})",
                status_code=503,
                retryable=True,
                provider=provider_name,
            )

        return provider, model_name

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Route a completion request. No automatic fallback — honor explicit provider choice."""
        provider, model_name = self.resolve(request.model)
        req = request.model_copy(update={"model": model_name})
        return await provider.complete(req)

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Route a streaming request."""
        provider, model_name = self.resolve(request.model)
        req = request.model_copy(update={"model": model_name})
        async for chunk in provider.stream(req):
            yield chunk

    async def list_all_models(self) -> list[ModelInfo]:
        """Aggregate models from all active providers."""
        models = []
        for provider in get_all_providers().values():
            if provider.is_healthy():
                try:
                    models.extend(await provider.list_models())
                except Exception:
                    logger.warning("Failed to list models for %s", provider.name)
        return models
