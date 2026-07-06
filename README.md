# LLM-Bridge

Local multi-model AI proxy gateway for **personal use**. Unifies Claude Code, Codex, and Gemini CLI behind a single OpenAI-compatible API with a built-in chat UI, reusing each tool's own subscription authentication.

> **Scope**: chat-only, single-user, localhost. Tool calling and multi-modal
> inputs are not supported. All providers go through official CLI/SDK
> harnesses — this gateway never extracts or replays OAuth tokens, which the
> providers have banned for third-party use (Anthropic enforced this in
> April 2026; note that headless Claude usage draws from monthly Agent SDK
> credits, not the interactive subscription pool).

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
Claude   Codex    Gemini
Agent    CLI       CLI
 SDK   (exec)   (stream-json)
```

| Provider | Harness | Models |
|----------|---------|--------|
| Claude | claude-agent-sdk (bundled CLI) | claude-sonnet-4-6, claude-opus-4-6 |
| Codex | `codex exec --json` subprocess | gpt-5.4, gpt-5.4-mini, gpt-5.3-codex, ... |
| Gemini | `gemini -p - --output-format stream-json` subprocess | gemini-3.1-pro-preview, gemini-2.5-pro, ... |

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- At least one CLI tool logged in: `claude`, `codex`, or `gemini`

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
claude/claude-sonnet-4-6       # Claude Sonnet via Agent SDK
claude/claude-opus-4-6         # Claude Opus via Agent SDK
codex/gpt-5.4                  # GPT-5.4 via Codex CLI
gemini/gemini-2.5-pro          # Gemini 2.5 Pro via CLI
```

Aliases (configurable in `config/default.yaml`): `sonnet`, `opus`, `gemini-pro`, `flash`.

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

- Model selector grouped by provider
- Per-conversation model selection
- Streaming output with sanitized markdown rendering and code highlighting
- Multiple concurrent conversations
- Conversation history (localStorage)
- Settings page (gear icon): API key for protected servers, default model for
  new chats, global system prompt, and a provider status panel with login hints

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
  gemini: { enabled: true }

routing:
  default_model: "claude/claude-sonnet-4-6"
  aliases:
    sonnet: "claude/claude-sonnet-4-6"
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
│   │   └── gemini.py           # Gemini CLI adapter
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
- CLI subprocess mode has higher latency than direct API calls (~3-8s per request for codex/gemini).
- Codex runs with `--ignore-user-config` by default (skips `~/.codex` skills/plugins, which
  otherwise add ~20k input tokens per request); set `providers.codex.ignore_user_config: false`
  to load your global Codex config.
- Codex model list is hardcoded (CLI has no list-models command).
- Gemini free tier has strict rate limits (429).
- Headless Claude usage is billed against monthly Agent SDK credits, not the interactive Claude Code pool.

## License

MIT
