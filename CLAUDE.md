# CLAUDE.md

Development context for Claude Code working on this project.

## What This Project Does

LLM-Bridge is a local AI proxy gateway that wraps multiple AI CLI tools (Claude Code, Codex, Gemini CLI, Antigravity) behind a single OpenAI-compatible API. It uses CLI subprocess mode for authentication — each provider's CLI handles its own OAuth/token management.

## Tech Stack

- **Python 3.12+** with **uv** for package management
- **FastAPI** + **uvicorn** for the API server
- **httpx** for async HTTP (Antigravity provider)
- **Pydantic v2** for request/response models
- Single-file HTML frontend (no build step) at `src/llm_bridge/web/static/index.html`

## Key Architecture Decisions

- **CLI subprocess mode** is the primary strategy for Claude, Codex, and Gemini. Prompts are sent via **stdin** (not command-line args) to avoid OS argument length limits.
- **OpenAI Chat Completions format** is the canonical internal format. Conversion only happens outbound.
- **Per-conversation streaming state** — multiple conversations can stream concurrently in the frontend.
- **No Redis/DB required** — rate limiting is in-memory, credentials are file-based.

## Provider CLI Commands

```
Claude:  claude --print --output-format stream-json --verbose --model {model} -p -
Codex:   codex exec --json --skip-git-repo-check --ephemeral -m {model} -
Gemini:  gemini -p - --output-format stream-json --model {model}
```

All read prompts from stdin. Output is line-delimited JSON.

## Running

```bash
uv sync                           # Install deps
uv run llm-bridge                 # Start server on :8787
uv run llm-bridge --debug         # Debug mode
uv run python scripts/test_providers.py --providers claude codex  # Test
```

## Code Layout

- `src/llm_bridge/providers/*.py` — One file per provider, all extend `BaseProvider`
- `src/llm_bridge/convert/` — Message format conversion (OpenAI <-> Anthropic/Gemini)
- `src/llm_bridge/gateway/` — Routing, rate limiting, middleware
- `src/llm_bridge/api/` — FastAPI route handlers
- `src/llm_bridge/web/static/index.html` — Single-file chat UI (vanilla JS, no framework)
- `config/default.yaml` — Default config; user override at `~/.llm-bridge/config.yaml`

## Known Limitations

- Antigravity provider needs its own OAuth credentials (Gemini CLI creds can't access Claude models through cloudcode-pa)
- Gemini free tier has strict rate limits (429)
- CLI subprocess mode has higher latency than direct API calls (~3-8s per request)
- Codex model list is hardcoded (CLI has no list-models command)
