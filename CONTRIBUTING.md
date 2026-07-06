# Contributing to LLM-Bridge

Thanks for your interest! This is a small, deliberately-scoped project. Please read the non-negotiables below before opening a PR.

## Non-negotiables

**1. Official harnesses only.** All inference goes through official CLI/SDK harnesses (claude-agent-sdk, `codex exec`, `agy -p`). PRs that read CLI credential files and call vendor backend APIs directly will be closed — providers ban that pattern (Anthropic enforced server-side blocks in 2026; Google's cloudcode endpoints return 403), and it puts users' accounts at risk.

**2. Chat-only scope.** Tool calling, vision, and multi-user serving features are out of scope. The value of this project is doing one thing reliably.

**3. Subprocess invariants** (codex.py / agy.py). When touching provider streaming code, preserve:
- **stderr must be DEVNULL on the streaming path** — nothing drains it there, and a full pipe buffer (~64 KB) deadlocks the child. `communicate()` drains it, so the non-streaming path may PIPE it for error detail.
- **`stream()` must kill+wait the child in a `finally` block** — client disconnects throw `GeneratorExit` at the `yield`; without cleanup you leak orphan CLI processes until the per-provider semaphore starves.
- **stdin: write → `drain()` → close.** Timeout paths must `kill()` then `await wait()`.

**4. Model output is untrusted input.** The frontend must keep DOMPurify between markdown rendering and `innerHTML`. Never add `ADD_ATTR`/`ADD_TAGS` allowances for event handlers.

**5. Config is either wired or deleted.** Every key in `src/llm_bridge/default.yaml` must be read somewhere in `web/app.py` (or wherever it's consumed). No aspirational config.

## Development setup

```bash
git clone https://github.com/mahui/llm-bridge.git && cd llm-bridge
uv sync
uv run llm-bridge --debug
```

## Checks before a PR

```bash
uv run ruff check src scripts          # lint (CI-enforced)
uv build                               # packaging must stay green (CI-enforced)

# Integration smoke tests — need a running server and at least one logged-in CLI.
# Use the cheapest models; they hit real quota.
uv run python scripts/test_providers.py --providers claude codex

# After touching provider code: verify no orphan processes
ps aux | grep -E "codex exec|agy -p" | grep -v grep
```

There are no unit tests yet — `convert/` and the SSE chunk utilities are the most valuable place to add them (pure logic, no credentials needed). Contributions welcome.

## Code layout

```
src/llm_bridge/
├── providers/     # one file per provider, all extend BaseProvider
├── convert/       # OpenAI chunk/streaming utilities
├── gateway/       # routing, middleware (auth, logging, errors)
├── api/           # FastAPI route handlers
├── web/           # app factory + single-file chat UI (vanilla JS, no build step)
├── config.py      # YAML config loading (packaged default + user override)
└── default.yaml   # packaged default config
```

The frontend is intentionally a single HTML file with no build step. Keep it that way.

## AI-assisted development

`CLAUDE.md` carries the project context for AI coding assistants, and `.claude/agents/` defines three review roles (info-architect, provider-engineer, gateway-verifier). If you use Claude Code, they'll be picked up automatically; if not, they're worth reading as review checklists.

## Releases (maintainers)

Tag a version to build and publish a GitHub Release with the wheel:

```bash
git tag v0.1.0 && git push origin v0.1.0
```
