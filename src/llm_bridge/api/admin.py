"""Admin endpoints for health and status monitoring."""

from __future__ import annotations

from fastapi import APIRouter, Request

from llm_bridge.providers import get_all_providers

router = APIRouter(prefix="/admin")


@router.get("/health")
async def health(request: Request):
    """Health check with per-provider status."""
    providers_status = {}
    for name, provider in get_all_providers().items():
        providers_status[name] = {
            "status": provider.status.value,
            "healthy": provider.is_healthy(),
        }
    return {
        "status": "ok",
        "providers": providers_status,
    }
