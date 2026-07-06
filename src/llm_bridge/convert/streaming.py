"""OpenAI streaming chunk utilities."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from llm_bridge.models import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    UsageInfo,
)


def new_chunk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


@dataclass
class StreamState:
    """Tracks state across a streaming session."""
    chunk_id: str = field(default_factory=new_chunk_id)
    created: int = field(default_factory=lambda: int(time.time()))
    model: str = ""
    sent_role: bool = False


def make_role_chunk(state: StreamState) -> ChatCompletionChunk:
    """Create the first chunk with role=assistant."""
    state.sent_role = True
    return ChatCompletionChunk(
        id=state.chunk_id,
        created=state.created,
        model=state.model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(role="assistant"),
            )
        ],
    )


def make_content_chunk(content: str, state: StreamState) -> ChatCompletionChunk:
    """Create a chunk with content delta."""
    return ChatCompletionChunk(
        id=state.chunk_id,
        created=state.created,
        model=state.model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(content=content),
            )
        ],
    )


def make_final_chunk(
    state: StreamState,
    finish_reason: str = "stop",
    usage: UsageInfo | None = None,
) -> ChatCompletionChunk:
    """Create the final chunk with finish_reason."""
    return ChatCompletionChunk(
        id=state.chunk_id,
        created=state.created,
        model=state.model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def format_sse(chunk: ChatCompletionChunk) -> str:
    """Format a chunk as an SSE data line."""
    return f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


def format_done() -> str:
    """Format the SSE [DONE] sentinel."""
    return "data: [DONE]\n\n"
