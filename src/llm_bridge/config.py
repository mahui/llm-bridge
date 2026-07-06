"""Configuration management with YAML loading and environment variable support."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    api_key: str = ""


class AntigravityProviderConfig(BaseModel):
    enabled: bool = True
    api_base: str = "https://cloudcode-pa.googleapis.com"
    project_id: str = ""


class ClaudeProviderConfig(BaseModel):
    enabled: bool = True
    mode: str = "cli"
    cli_path: str = "claude"
    api_key: str = ""


class CodexProviderConfig(BaseModel):
    enabled: bool = True
    api_base: str = "https://api.openai.com"
    auth_file: str = "~/.codex/auth.json"


class GeminiProviderConfig(BaseModel):
    enabled: bool = True
    api_base: str = "https://cloudcode-pa.googleapis.com"
    auth_file: str = "~/.gemini/oauth_creds.json"


class ProvidersConfig(BaseModel):
    antigravity: AntigravityProviderConfig = Field(default_factory=AntigravityProviderConfig)
    claude: ClaudeProviderConfig = Field(default_factory=ClaudeProviderConfig)
    codex: CodexProviderConfig = Field(default_factory=CodexProviderConfig)
    gemini: GeminiProviderConfig = Field(default_factory=GeminiProviderConfig)


class RoutingConfig(BaseModel):
    default_model: str = "claude/claude-sonnet-4-6"
    aliases: dict[str, str] = Field(default_factory=dict)


class ProviderRateLimit(BaseModel):
    rpm: int = 60
    max_concurrent: int = 3


class RateLimitingConfig(BaseModel):
    enabled: bool = True
    default_rpm: int = 60
    per_provider: dict[str, ProviderRateLimit] = Field(default_factory=dict)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    track_usage: bool = True
    usage_log: str = "~/.llm-bridge/usage.jsonl"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class BridgeConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    rate_limiting: RateLimitingConfig = Field(default_factory=RateLimitingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_config: BridgeConfig | None = None


def _substitute_env_vars(obj: object) -> object:
    """Recursively replace ${VAR} patterns with environment variable values."""
    if isinstance(obj, str):
        if obj.startswith("${") and obj.endswith("}"):
            return os.environ.get(obj[2:-1], "")
        return obj
    if isinstance(obj, dict):
        return {k: _substitute_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_env_vars(v) for v in obj]
    return obj


def load_config(path: str | Path | None = None) -> BridgeConfig:
    """Load configuration from YAML file with env-var substitution."""
    global _config

    if path is None:
        # Try user config first, then default
        user_path = Path.home() / ".llm-bridge" / "config.yaml"
        default_path = Path(__file__).resolve().parent.parent.parent / "config" / "default.yaml"
        path = user_path if user_path.exists() else default_path

    path = Path(path)
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        raw = _substitute_env_vars(raw)
        _config = BridgeConfig.model_validate(raw)
    else:
        _config = BridgeConfig()

    # Apply env-var overrides
    env_key = os.environ.get("LLM_BRIDGE_API_KEY")
    if env_key:
        _config.server.api_key = env_key

    return _config


def get_config() -> BridgeConfig:
    """Get the current config singleton, loading defaults if needed."""
    global _config
    if _config is None:
        return load_config()
    return _config
