# LLM-Bridge

Local multi-model AI proxy gateway. Unifies Codex, Claude Code, Gemini CLI, and Antigravity behind a single OpenAI-compatible API with a built-in chat UI.

## Architecture

```
Clients (Web UI / curl / OpenAI SDK)
          |
    OpenAI-compatible API (:8787)
          |
    Gateway (routing, rate limiting, middleware)
          |
  +-------+-------+--------+-----------+
  |       |       |        |           |
Claude  Codex   Gemini  Antigravity
 CLI     CLI     CLI    cloudcode API
```

All four providers use **CLI subprocess** mode as the primary strategy, leveraging each tool's built-in authentication:

| Provider | Command | Models |
|----------|---------|--------|
| Claude | `claude --print --output-format stream-json` | claude-sonnet-4-6, claude-opus-4-6 |
| Codex | `codex exec --json` | gpt-5.4, gpt-5.4-mini, gpt-5.3-codex, ... |
| Gemini | `gemini -p --output-format stream-json` | gemini-3.1-pro-preview, gemini-3-flash-preview, gemini-2.5-pro, ... |
| Antigravity | Direct HTTP to cloudcode-pa API | claude-sonnet-4-6-thinking, gemini-3.1-pro-high, ... |

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- At least one CLI tool installed and logged in: `claude`, `codex`, or `gemini`

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
uv run llm-bridge --host 0.0.0.0    # Listen on all interfaces
uv run llm-bridge --debug           # Debug logging
uv run llm-bridge --config my.yaml  # Custom config file
```

## API Usage

### OpenAI-Compatible Endpoints

**Chat Completions** (streaming & non-streaming):

```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude/claude-sonnet-4-6",
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
claude/claude-sonnet-4-6       # Claude Sonnet via CLI
claude/claude-opus-4-6         # Claude Opus via CLI
codex/gpt-5.4                  # GPT-5.4 via Codex CLI
codex/gpt-5.4-mini             # GPT-5.4 Mini via Codex CLI
gemini/gemini-2.5-pro          # Gemini 2.5 Pro via CLI
gemini/gemini-3.1-pro-preview  # Gemini 3.1 Pro via CLI
antigravity/claude-sonnet-4-6  # Claude via Cloud Code Assist API
```

Aliases are available (configurable in `config/default.yaml`):

```
sonnet     -> claude/claude-sonnet-4-6
opus       -> claude/claude-opus-4-6
gemini-pro -> gemini/gemini-2.5-pro
flash      -> gemini/gemini-2.5-flash
```

### OpenAI SDK Integration

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8787/v1",
    api_key="unused",  # No API key required by default
)

response = client.chat.completions.create(
    model="claude/claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

## Web UI

Built-in ChatGPT-style interface at `http://localhost:8787`:

- Model selector with all available models grouped by provider
- Per-conversation model selection
- Streaming output with markdown rendering and code highlighting
- Multiple concurrent conversations
- Conversation history (localStorage)

## Management Endpoints

```bash
# Provider health status
curl http://localhost:8787/admin/health

# Authentication status
curl http://localhost:8787/auth/status

# Swagger API docs
open http://localhost:8787/docs
```

## Configuration

Default config at `config/default.yaml`. Override with `~/.llm-bridge/config.yaml` or `--config` flag.

```yaml
server:
  host: "127.0.0.1"
  port: 8787
  api_key: ""  # Set to protect the API; or use LLM_BRIDGE_API_KEY env var

providers:
  claude:
    enabled: true
    cli_path: "claude"
  codex:
    enabled: true
  gemini:
    enabled: true
  antigravity:
    enabled: true

routing:
  default_model: "claude/claude-sonnet-4-6"
  aliases:
    sonnet: "claude/claude-sonnet-4-6"

rate_limiting:
  enabled: true
  per_provider:
    claude: { rpm: 10, max_concurrent: 1 }
    codex: { rpm: 20, max_concurrent: 2 }
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
│   │   ├── claude.py           # Claude Code CLI adapter
│   │   ├── codex.py            # Codex CLI adapter
│   │   ├── gemini.py           # Gemini CLI adapter
│   │   └── antigravity.py      # Cloud Code Assist API adapter
│   ├── auth/
│   │   ├── manager.py          # Credential lifecycle management
│   │   ├── cli_detect.py       # Auto-detect CLI credentials
│   │   ├── oauth.py            # Google OAuth PKCE flow
│   │   └── store.py            # Encrypted credential storage
│   ├── convert/
│   │   ├── openai.py           # OpenAI format (canonical)
│   │   ├── anthropic.py        # Anthropic format conversion
│   │   ├── gemini.py           # Gemini format conversion
│   │   └── streaming.py        # SSE parser & chunk utilities
│   ├── gateway/
│   │   ├── router.py           # Model routing & resolution
│   │   ├── rate_limiter.py     # Token-bucket rate limiter
│   │   └── middleware.py       # Auth, logging, error handling
│   ├── api/
│   │   ├── chat.py             # /v1/chat/completions
│   │   ├── models.py           # /v1/models
│   │   ├── auth.py             # /auth/*
│   │   └── admin.py            # /admin/*
│   └── web/
│       ├── app.py              # FastAPI application factory
│       └── static/index.html   # Chat UI
├── scripts/
│   └── test_providers.py       # Provider integration tests
└── tests/
```

## Testing

```bash
# Start the server first
uv run llm-bridge &

# Run all provider tests
uv run python scripts/test_providers.py

# Test specific providers
uv run python scripts/test_providers.py --providers claude codex

# Streaming only / non-streaming only
uv run python scripts/test_providers.py --stream-only
uv run python scripts/test_providers.py --no-stream
```

## License

MIT
