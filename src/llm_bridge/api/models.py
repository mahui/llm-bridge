"""GET /v1/models endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from llm_bridge.models import ModelListResponse

if TYPE_CHECKING:
    from llm_bridge.gateway.router import ModelRouter

router = APIRouter()


@router.get("/v1/models")
async def list_models(request: Request):
    """List all available models across providers."""
    model_router: ModelRouter = request.app.state.router
    models = await model_router.list_all_models()
    return ModelListResponse(data=models).model_dump()
