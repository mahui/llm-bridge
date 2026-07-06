"""POST /v1/chat/completions endpoint."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from llm_bridge.convert.openai import serialize_response
from llm_bridge.convert.streaming import format_done, format_sse
from llm_bridge.models import ChatCompletionRequest, ErrorDetail, ErrorResponse
from llm_bridge.providers.base import ProviderError

if TYPE_CHECKING:
    from llm_bridge.gateway.router import ModelRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint."""
    model_router: ModelRouter = request.app.state.router

    if body.stream:
        # Validate routing before starting the stream
        try:
            model_router.resolve(body.model)
        except ProviderError as e:
            err = ErrorResponse(
                error=ErrorDetail(message=e.message, type="provider_error", code=e.provider)
            )
            return JSONResponse(status_code=e.status_code, content=err.model_dump())

        return StreamingResponse(
            _stream_response(model_router, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    response = await model_router.complete(body)
    return serialize_response(response)


async def _stream_response(router: ModelRouter, request: ChatCompletionRequest):
    """Async generator that yields SSE formatted chunks."""
    try:
        async for chunk in router.stream(request):
            yield format_sse(chunk)
        yield format_done()
    except ProviderError as e:
        # Send error as SSE event so the client can see it
        error_data = {"error": {"message": e.message, "type": "provider_error", "code": e.provider}}
        yield f"data: {json.dumps(error_data)}\n\n"
        yield format_done()
    except Exception as e:
        logger.exception("Stream error")
        error_data = {"error": {"message": str(e), "type": "server_error"}}
        yield f"data: {json.dumps(error_data)}\n\n"
        yield format_done()
