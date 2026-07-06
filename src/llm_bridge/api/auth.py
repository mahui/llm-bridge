"""Auth status and login endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from llm_bridge.auth.manager import AuthManager

router = APIRouter(prefix="/auth")


@router.get("/status")
async def auth_status(request: Request):
    """Return authentication status for all providers."""
    auth_manager: AuthManager = request.app.state.auth_manager
    return auth_manager.get_status()


@router.post("/login/{provider}")
async def login(provider: str, request: Request):
    """Initiate login flow for a provider."""
    if provider == "antigravity":
        from llm_bridge.auth.oauth import GoogleOAuthPKCE

        oauth = GoogleOAuthPKCE()
        auth_url, code_verifier, state = oauth.generate_auth_url()
        # Store state for callback verification
        request.app.state.oauth_pending = {
            "code_verifier": code_verifier,
            "state": state,
            "provider": provider,
        }
        return {"auth_url": auth_url, "state": state}
    elif provider in ("codex", "claude", "gemini"):
        cli_cmds = {
            "codex": "codex",
            "claude": "claude",
            "gemini": "gemini",
        }
        return {
            "message": f"Please run `{cli_cmds[provider]}` in terminal to login first, "
            "then restart the server to detect credentials."
        }
    return {"error": f"Unknown provider: {provider}"}


@router.post("/refresh/{provider}")
async def refresh(provider: str, request: Request):
    """Force credential refresh for a provider."""
    auth_manager: AuthManager = request.app.state.auth_manager
    try:
        await auth_manager.get_access_token(provider)
        return {"status": "ok", "provider": provider}
    except Exception as e:
        return {"status": "error", "provider": provider, "message": str(e)}
