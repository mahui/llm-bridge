# CLAUDE.md

Development context for Claude Code working on this project.

## What This Project Does

LLM-Bridge is a local AI proxy gateway for personal use that wraps three AI CLI harnesses (Claude Code via Agent SDK, Codex, Gemini CLI) behind a single OpenAI-compatible API. Each provider's official harness handles its own OAuth/token management — the gateway never touches tokens.

## Scope (deliberate)

- **Chat-only**: tool calling and multi-modal content are out of scope.
- **Single-user, localhost**: no rate limiting, no credential storage, CORS locked to the built-in UI's origin.
- **Official harnesses only**: direct backend-API access with extracted CLI tokens was removed in July 2026 — providers ban it (Anthropic server-side since Jan 2026) and the endpoints return 403. Do not reintroduce it.

## Tech Stack

- **Python 3.12+** with **uv** for package management
- **FastAPI** + **uvicorn** for the API server
- **claude-agent-sdk** for the Claude provider (bundles its own CLI)
- **Pydantic v2** for request/response models
- Single-file HTML frontend (no build step) at `src/llm_bridge/web/static/index.html`

## Key Architecture Decisions

- **OpenAI Chat Completions format** is the canonical internal format.
- **Claude** goes through `claude_agent_sdk.query()` (tools disabled, max_turns=1, `include_partial_messages` for token-level streaming). The SDK owns subprocess lifecycle.
- **`reasoning_effort`** (OpenAI-standard request field, low/medium/high/xhigh) is honored by claude (SDK `effort` option; gateway defaults to medium, NOT the SDK's high) and codex (`-c model_reasoning_effort`); ignored by gemini. UI settings expose it.
- **Codex/Gemini** use CLI subprocesses. Prompts are sent via **stdin** (not args) to avoid OS argument length limits. Streaming paths use `stderr=DEVNULL` (an undrained pipe deadlocks the child) and a `finally` block that kills the child on client disconnect — keep both invariants when editing.
- **Per-conversation streaming state** — multiple conversations can stream concurrently in the frontend.
- Frontend renders model output through **DOMPurify** after marked — model output is untrusted input; never bypass the sanitizer.

## Provider CLI Commands

```
Codex:   codex exec --json --skip-git-repo-check --ephemeral --ignore-user-config -m {model} -
Gemini:  gemini -p - --output-format stream-json --model {model}
```

`--ignore-user-config` keeps the user's `~/.codex` skills/plugins/reasoning
settings out of gateway requests (~20k input tokens and xhigh-reasoning
latency otherwise); auth still comes from `~/.codex`. Configurable via
`providers.codex.ignore_user_config`.

Both read prompts from stdin. Output is line-delimited JSON.

## Running

```bash
uv sync                           # Install deps
uv run llm-bridge                 # Start server on :8787
uv run llm-bridge --debug         # Debug mode
uv run python scripts/test_providers.py --providers claude codex  # Smoke test (needs running server)
```

## Code Layout

- `src/llm_bridge/providers/*.py` — One file per provider, all extend `BaseProvider`
- `src/llm_bridge/convert/` — OpenAI chunk/streaming utilities
- `src/llm_bridge/gateway/` — Routing, middleware
- `src/llm_bridge/api/` — FastAPI route handlers
- `src/llm_bridge/web/static/index.html` — Single-file chat UI (vanilla JS, no framework)
- `config/default.yaml` — Default config; user override at `~/.llm-bridge/config.yaml`

## Agent Roles (.claude/agents/)

- **info-architect** — 信息架构审查：配置/协议/命名/文档/UI 五条一致性链路。新增配置项、请求字段、改模型清单或文档的改动都应过一遍。本项目最高发的问题类别（配置未接线、文档漂移、静默丢弃字段）由它把关。
- **provider-engineer** — provider 适配层：子进程三不变量（stderr DEVNULL / finally kill / stdin drain）、SDK/CLI 升级适配、合规红线（禁 token 直连）。
- **gateway-verifier** — 端到端验证：重启服务器、冒烟测试、浏览器 UI 验证、孤儿进程检查。只验证不修码。

## Known Limitations

- Chat-only (no tools/vision); requests with `role="tool"` messages are flattened away
- CLI subprocess latency ~3-8s per request (codex/gemini)
- Model lists: codex reads `$CODEX_HOME/models_cache.json` (CLI-maintained); claude uses the
  free Anthropic Models API when an API key is configured (listing only, never inference),
  else hardcoded fallback; gemini hardcoded (no headless list command)
- Gemini free tier has strict rate limits (429)
- Headless Claude usage bills against monthly Agent SDK credits, not the interactive pool
