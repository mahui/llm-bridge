# LLM-Bridge

Local multi-model AI proxy gateway for **personal use**. Unifies Claude Code, Codex, and the Antigravity CLI behind a single OpenAI-compatible API with a built-in chat UI, reusing each tool's own subscription authentication.

> **Scope**: chat-only, single-user, localhost. Tool calling and multi-modal
> inputs are not supported. All providers go through official CLI/SDK
> harnesses — this gateway never extracts or replays OAuth tokens, which the
> providers have banned for third-party use (Anthropic added server-side
> blocks in January 2026 and fully enforced the ban on April 4, 2026; note
> that headless Claude usage draws from monthly Agent SDK credits, not the
> interactive subscription pool).

## Architecture

```
Clients (Web UI / curl / OpenAI SDK)
          |
    OpenAI-compatible API (:8787)
          |
    Gateway (routing, middleware)
          |
  +-------+---------+
  |       |         |
Claude   Codex  Antigravity
Agent    CLI       CLI
 SDK   (exec)    (agy -p)
```

| Provider | Harness | Models |
|----------|---------|--------|
| Claude | claude-agent-sdk (bundled CLI) | claude-fable-5, claude-opus-4-8, claude-sonnet-5, claude-haiku-4-5 |
| Codex | `codex exec --json` subprocess | gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.3-codex-spark |
| Antigravity | `agy -p -` subprocess | gemini-3.5-flash*, gemini-3.1-pro*, claude-*-thinking, gpt-oss-120b (dynamic via `agy models`) |

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- At least one CLI tool logged in: `claude`, `codex`, or `agy` (Antigravity)

### Install & Run

```bash
git clone <repo-url> && cd agent-proxy

# Install dependencies
uv sync

# Start the server
uv run llm-bridge

# Open http://127.0.0.1:8787 in your browser
```

### CLI Options

```bash
uv run llm-bridge                    # Default: 127.0.0.1:8787
uv run llm-bridge --port 9000       # Custom port
uv run llm-bridge --debug           # Debug logging
uv run llm-bridge --config my.yaml  # Custom config file
```

## API Usage

**Chat Completions** (streaming & non-streaming):

```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude/claude-sonnet-5",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

**List Models**:

```bash
curl http://localhost:8787/v1/models
```

### Model Naming

Models use `provider/model-name` format:

```
claude/claude-fable-5          # Claude Fable 5 via Agent SDK
claude/claude-sonnet-5         # Claude Sonnet 5 via Agent SDK
codex/gpt-5.4                  # GPT-5.4 via Codex CLI
agy/gemini-3.5-flash-medium    # Gemini via Antigravity CLI
agy/claude-sonnet-4.6-thinking # Claude (thinking) via Antigravity CLI
```

Aliases (configurable in `config/default.yaml`): `fable`, `opus`, `sonnet`, `haiku`, `gemini-pro`, `flash`.

### OpenAI SDK Integration

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8787/v1",
    api_key="unused",  # No API key required by default
)

response = client.chat.completions.create(
    model="claude/claude-sonnet-5",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

## Web UI

Built-in ChatGPT-style interface at `http://localhost:8787`:

- Model selector grouped by provider
- Per-conversation model selection
- Streaming output with sanitized markdown rendering and code highlighting
- Multiple concurrent conversations
- Conversation history (localStorage)
- Settings page (gear icon): API key for protected servers, default model for
  new chats, global system prompt, reasoning-effort level, and a provider
  status panel with login hints

## Management Endpoints

```bash
curl http://localhost:8787/admin/health   # Provider health
curl http://localhost:8787/auth/status    # Harness availability + login hints
open http://localhost:8787/docs           # Swagger API docs
```

## Configuration

Default config at `config/default.yaml`. Override with `~/.llm-bridge/config.yaml` or `--config`.

```yaml
server:
  host: "127.0.0.1"
  port: 8787
  api_key: ""  # Set to protect the API; or use LLM_BRIDGE_API_KEY env var

providers:
  claude: { enabled: true }
  codex: { enabled: true }
  agy: { enabled: true }

routing:
  default_model: "claude/claude-sonnet-5"
  aliases:
    sonnet: "claude/claude-sonnet-5"
```

## Project Structure

```
agent-proxy/
├── pyproject.toml              # Project config & dependencies
├── config/default.yaml         # Default configuration
├── src/llm_bridge/
│   ├── main.py                 # CLI entry point
│   ├── config.py               # YAML config loader
│   ├── models.py               # OpenAI-compatible Pydantic models
│   ├── providers/
│   │   ├── base.py             # Provider abstract base class
│   │   ├── claude.py           # claude-agent-sdk adapter
│   │   ├── codex.py            # Codex CLI adapter
│   │   └── agy.py              # Antigravity CLI adapter
│   ├── convert/
│   │   ├── openai.py           # OpenAI format (canonical)
│   │   └── streaming.py        # Streaming chunk utilities
│   ├── gateway/
│   │   ├── router.py           # Model routing & resolution
│   │   └── middleware.py       # Auth, logging, error handling
│   ├── api/
│   │   ├── chat.py             # /v1/chat/completions
│   │   ├── models.py           # /v1/models
│   │   ├── auth.py             # /auth/* (status + login hints)
│   │   └── admin.py            # /admin/*
│   └── web/
│       ├── app.py              # FastAPI application factory
│       └── static/index.html   # Chat UI
├── scripts/
│   └── test_providers.py       # Provider integration smoke tests
└── tests/                      # (unit tests: TODO)
```

## Testing

Integration smoke tests (require a running server and logged-in CLIs):

```bash
uv run llm-bridge &
uv run python scripts/test_providers.py                       # All providers
uv run python scripts/test_providers.py --providers claude    # One provider
uv run python scripts/test_providers.py --stream-only
```

## Known Limitations

- **Chat-only**: `tools` / function calling and multi-modal message content are not supported.
- Supports the OpenAI `reasoning_effort` request field (minimal/low/medium/high/xhigh/max,
  mapped to each provider's native range); honored by claude (default medium) and codex,
  ignored by agy (its models encode depth in their variant names). Other sampling params (`temperature`, `max_tokens`, ...) are accepted
  but not forwarded — CLI harnesses don't expose them.
- CLI subprocess mode has higher latency than direct API calls (~3-8s per request for codex/agy).
- Codex runs with `--ignore-user-config` by default (skips `~/.codex` skills/plugins, which
  otherwise add ~20k input tokens per request); set `providers.codex.ignore_user_config: false`
  to load your global Codex config.
- Model lists: Codex reads the CLI's own `models_cache.json`; Claude uses the free Models API
  when `providers.claude.api_key` / `ANTHROPIC_API_KEY` is set (never used for inference),
  otherwise a hardcoded fallback; Antigravity is fully dynamic via `agy models`.
- Antigravity output is plain text: no token usage accounting, chunk-level (not token-level)
  streaming, and reasoning depth is chosen via model variants (`-low/-medium/-high`) rather
  than the `reasoning_effort` field.
- Headless Claude usage is billed against monthly Agent SDK credits, not the interactive Claude Code pool.

## License

MIT
