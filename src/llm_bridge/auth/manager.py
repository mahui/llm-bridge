"""Unified credential manager with auto-refresh and concurrent access control."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from llm_bridge.auth.cli_detect import detect_all
from llm_bridge.auth.oauth import GoogleOAuthPKCE

logger = logging.getLogger(__name__)

# Refresh buffer: refresh 5 minutes before expiry
REFRESH_BUFFER_SECONDS = 300

# Codex token refresh endpoint
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


@dataclass
class ProviderCredentials:
    provider: str
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0  # unix timestamp
    extra: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False  # No expiry info, assume valid
        return time.time() >= (self.expires_at - REFRESH_BUFFER_SECONDS)


class AuthManager:
    """Manages credentials for all providers with auto-refresh."""

    def __init__(self) -> None:
        self._credentials: dict[str, ProviderCredentials] = {}
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, provider: str) -> asyncio.Lock:
        if provider not in self._refresh_locks:
            self._refresh_locks[provider] = asyncio.Lock()
        return self._refresh_locks[provider]

    async def initialize(self) -> None:
        """Detect and load credentials from all installed CLIs."""
        detected = detect_all()
        for provider, creds in detected.items():
            if creds is None:
                logger.info("No credentials found for %s", provider)
                continue
            self._credentials[provider] = ProviderCredentials(
                provider=provider,
                access_token=creds.get("access_token", ""),
                refresh_token=creds.get("refresh_token", ""),
                expires_at=self._parse_expiry(creds),
                extra=creds,
            )
            logger.info("Loaded credentials for %s (source: %s)", provider, creds.get("source"))

    @staticmethod
    def _parse_expiry(creds: dict) -> float:
        """Parse expiry from various credential formats."""
        # Claude: expiresAt in milliseconds
        if "expires_at" in creds and creds["expires_at"] > 1e12:
            return creds["expires_at"] / 1000.0
        if "expires_at" in creds:
            return float(creds["expires_at"])
        # Gemini: expiry_date in milliseconds
        if "expiry_date" in creds:
            return creds["expiry_date"] / 1000.0
        return 0.0

    async def get_access_token(self, provider: str) -> str:
        """Get a valid access token, refreshing if needed."""
        creds = self._credentials.get(provider)
        if creds is None:
            raise ValueError(f"No credentials for provider: {provider}")

        if creds.is_expired:
            async with self._get_lock(provider):
                # Double-check after acquiring lock
                creds = self._credentials[provider]
                if creds.is_expired:
                    await self._refresh(provider)
                    creds = self._credentials[provider]

        return creds.access_token

    async def _refresh(self, provider: str) -> None:
        """Refresh credentials for a provider."""
        creds = self._credentials[provider]
        logger.info("Refreshing credentials for %s", provider)

        try:
            if provider in ("gemini", "antigravity"):
                await self._refresh_google(provider)
            elif provider == "codex":
                await self._refresh_codex()
            elif provider == "claude":
                await self._refresh_claude()
        except Exception:
            logger.exception("Failed to refresh credentials for %s", provider)
            raise

    async def _refresh_google(self, provider: str) -> None:
        """Refresh Google OAuth tokens (Gemini/Antigravity)."""
        creds = self._credentials[provider]
        result = await GoogleOAuthPKCE.refresh_access_token(creds.refresh_token)
        creds.access_token = result["access_token"]
        creds.expires_at = time.time() + result.get("expires_in", 3600)

        # Also refresh the paired provider (gemini <-> antigravity share creds)
        other = "antigravity" if provider == "gemini" else "gemini"
        if other in self._credentials:
            other_creds = self._credentials[other]
            other_creds.access_token = creds.access_token
            other_creds.expires_at = creds.expires_at

    async def _refresh_codex(self) -> None:
        """Refresh Codex OAuth token via auth.openai.com."""
        creds = self._credentials["codex"]
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                CODEX_TOKEN_URL,
                json={
                    "refresh_token": creds.refresh_token,
                    "grant_type": "refresh_token",
                    "client_id": creds.extra.get("client_id", ""),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            creds.access_token = data["access_token"]
            creds.expires_at = time.time() + data.get("expires_in", 3600)

    async def _refresh_claude(self) -> None:
        """Re-detect Claude credentials (CLI manages its own refresh)."""
        from llm_bridge.auth.cli_detect import detect_claude_credentials

        new_creds = detect_claude_credentials()
        if new_creds:
            creds = self._credentials["claude"]
            creds.access_token = new_creds.get("access_token", "")
            creds.expires_at = self._parse_expiry(new_creds)

    def is_authenticated(self, provider: str) -> bool:
        creds = self._credentials.get(provider)
        return creds is not None and bool(creds.access_token)

    def get_status(self) -> dict[str, dict]:
        """Return auth status for all providers."""
        result = {}
        for provider in ("codex", "claude", "gemini", "antigravity"):
            creds = self._credentials.get(provider)
            result[provider] = {
                "authenticated": creds is not None and bool(creds.access_token),
                "expires_at": creds.expires_at if creds else None,
                "source": creds.extra.get("source", "") if creds else "",
            }
        return result

    def set_credentials(self, provider: str, creds: ProviderCredentials) -> None:
        """Manually set credentials for a provider."""
        self._credentials[provider] = creds
