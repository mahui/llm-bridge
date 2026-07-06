"""OpenAI format normalization (canonical internal format)."""

from __future__ import annotations

from llm_bridge.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


def normalize_request(raw: dict) -> ChatCompletionRequest:
    """Validate and normalize an incoming request dict."""
    return ChatCompletionRequest.model_validate(raw)


def serialize_response(response: ChatCompletionResponse) -> dict:
    """Serialize a response to dict for JSON output."""
    return response.model_dump(exclude_none=True)


def serialize_chunk(chunk: ChatCompletionChunk) -> str:
    """Serialize a streaming chunk to JSON string for SSE."""
    return chunk.model_dump_json(exclude_none=True)
