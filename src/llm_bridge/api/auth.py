"""Auth status endpoints.

Authentication is delegated entirely to each provider's official CLI/SDK
harness — this gateway never reads or refreshes OAuth tokens itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from llm_bridge.providers import get_all_providers

router = APIRouter(prefix="/auth")

LOGIN_HINTS = {
    "claude": "Run `claude` in a terminal and complete login (or `claude setup-token`).",
    "codex": "Run `codex login` in a terminal.",
    "gemini": "Run `gemini` in a terminal and complete login.",
}


@router.get("/status")
async def auth_status():
    """Report per-provider harness availability (auth lives in each CLI)."""
    result = {}
    for name, provider in get_all_providers().items():
        result[name] = {
            "status": provider.status.value,
            "healthy": provider.is_healthy(),
            "login_hint": LOGIN_HINTS.get(name, ""),
        }
    return result


@router.post("/login/{provider}")
async def login(provider: str):
    """Point the user at the provider CLI's own login flow."""
    hint = LOGIN_HINTS.get(provider)
    if hint is None:
        return {"error": f"Unknown provider: {provider}"}
    return {"message": f"{hint} Then restart the server."}
