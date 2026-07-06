"""Abstract base class for AI provider adapters."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import AsyncIterator

from llm_bridge.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
)


class ProviderStatus(enum.Enum):
    READY = "ready"
    INITIALIZING = "initializing"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        retryable: bool = False,
        provider: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.provider = provider


class BaseProvider(ABC):
    """Abstract base class that every provider adapter must implement."""

    def __init__(self) -> None:
        self._status = ProviderStatus.INITIALIZING

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'claude', 'codex', 'gemini')."""

    @property
    def status(self) -> ProviderStatus:
        return self._status

    @abstractmethod
    async def initialize(self) -> None:
        """One-time async initialization (auth, connection pools)."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Cleanup resources."""

    @abstractmethod
    async def complete(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """Non-streaming completion."""

    @abstractmethod
    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Streaming completion yielding OpenAI-format chunks."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return available models for this provider."""

    async def health_check(self) -> bool:
        """Check if provider is available. Override for custom checks."""
        return self._status == ProviderStatus.READY

    def is_healthy(self) -> bool:
        return self._status == ProviderStatus.READY
