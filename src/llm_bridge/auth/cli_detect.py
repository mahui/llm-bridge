"""Auto-detection of CLI credential files for each provider."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_codex_credentials() -> dict | None:
    """Read Codex OAuth credentials from ~/.codex/auth.json."""
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        logger.debug("Codex auth.json not found at %s", auth_path)
        return None
    try:
        data = json.loads(auth_path.read_text())
        tokens = data.get("tokens", data)
        return {
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "id_token": tokens.get("id_token", ""),
            "source": str(auth_path),
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse Codex auth.json: %s", e)
        return None


def detect_claude_credentials() -> dict | None:
    """Read Claude Code credentials from ~/.claude/.credentials.json."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        logger.debug("Claude .credentials.json not found at %s", cred_path)
        return None
    try:
        data = json.loads(cred_path.read_text())
        oauth = data.get("claudeAiOauth", {})
        if not oauth:
            return None
        return {
            "access_token": oauth.get("accessToken", ""),
            "refresh_token": oauth.get("refreshToken", ""),
            "expires_at": oauth.get("expiresAt", 0),
            "source": str(cred_path),
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse Claude credentials: %s", e)
        return None


def detect_gemini_credentials() -> dict | None:
    """Read Gemini CLI credentials from ~/.gemini/oauth_creds.json."""
    cred_path = Path.home() / ".gemini" / "oauth_creds.json"
    if not cred_path.exists():
        logger.debug("Gemini oauth_creds.json not found at %s", cred_path)
        return None
    try:
        data = json.loads(cred_path.read_text())
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expiry_date": data.get("expiry_date", 0),
            "scope": data.get("scope", ""),
            "source": str(cred_path),
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse Gemini credentials: %s", e)
        return None


def detect_antigravity_credentials() -> dict | None:
    """Try to detect Antigravity credentials.

    Antigravity stores creds in macOS Keychain "Antigravity Safe Storage".
    As a fallback, we reuse Gemini CLI credentials since both use the
    same cloudcode-pa.googleapis.com API.
    """
    # Attempt Gemini CLI creds as they share the same API
    gemini_creds = detect_gemini_credentials()
    if gemini_creds:
        logger.info("Using Gemini CLI credentials for Antigravity (shared API)")
        return {
            **gemini_creds,
            "source": f"gemini-shared:{gemini_creds['source']}",
        }
    logger.debug("No Antigravity credentials found")
    return None


def detect_all() -> dict[str, dict | None]:
    """Detect credentials for all providers."""
    return {
        "codex": detect_codex_credentials(),
        "claude": detect_claude_credentials(),
        "gemini": detect_gemini_credentials(),
        "antigravity": detect_antigravity_credentials(),
    }
