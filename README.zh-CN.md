# LLM ⇌ BRIDGE

**把你的 AI 订阅统一成一个 API。** 本地个人网关：Claude Code、Codex、Antigravity CLI 汇聚到一个 OpenAI 兼容端点，自带聊天 UI。

[English](README.md) | [简体中文](README.zh-CN.md)

![Chat UI](docs/screenshot-chat.png)

## 为什么做这个

如果你同时订阅了 Claude、ChatGPT（Codex）和 Google Antigravity，额度就散落在三个 CLI、三套交互里。LLM-Bridge 把所有模型放到**一个 OpenAI 兼容 API** 和一个聊天界面后面，复用各家 CLI 自己的订阅登录——不用买 API key，也不碰任何 token。

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="unused")

for model in ["claude/claude-sonnet-5", "codex/gpt-5.5", "agy/gemini-3.5-flash-medium"]:
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "你好"}])
    print(model, "→", r.choices[0].message.content)
```

> **定位（如实声明）**：仅聊天（不支持 tool calling / 多模态）、单用户、默认仅本机。
> 全部流量走**官方 CLI/SDK harness**——本网关从不提取或重放 OAuth token
>（各厂商已封禁该行为：Anthropic 2026 年 1 月上线服务端拦截、4 月全面执行）。
> Claude 的 headless 用量计入每月 Agent SDK credit。

## Providers

| Provider | Harness | 模型 | 模型列表 |
|----------|---------|------|----------|
| **claude** | claude-agent-sdk（自带 CLI） | Fable 5、Opus 4.8、Sonnet 5、Haiku 4.5 | 配 key 走 Models API，否则静态 |
| **codex** | `codex exec --json` 子进程 | GPT-5.5、GPT-5.4、GPT-5.4-mini… | 动态（CLI 自身缓存） |
| **agy** | `agy -p -` 子进程（Antigravity） | Gemini 3.5/3.1、Claude 4.6 Thinking、GPT-OSS 120B | 动态（`agy models`） |

## 安装

**作为工具安装**（无需 clone）：

```bash
uv tool install git+https://github.com/mahui/llm-bridge
llm-bridge
```

或从[最新 Release](https://github.com/mahui/llm-bridge/releases/latest) 下载 wheel 后 `uv tool install ./llm_bridge-*.whl`。

**从源码**：

```bash
git clone https://github.com/mahui/llm-bridge.git && cd llm-bridge
uv sync
uv run llm-bridge
```

打开 **http://127.0.0.1:8787**。前置条件：Python 3.12+、[uv](https://github.com/astral-sh/uv)、至少一个已登录的 CLI（`claude` / `codex login` / `agy`）。

## Web UI

![API view](docs/screenshot-api.png)

- 流式聊天，按会话选模型，多会话并发
- Provider 信号色——每条回复左侧色轨一眼看出出自哪家
- **API 视图**：端点参考、跟随所选模型的 cURL/Python/JS 示例代码、页内 playground 试跑
- 设置：API key、默认模型、全局 system prompt、推理深度
- 日/夜主题（跟随系统，顶栏切换）
- Markdown + 代码高亮，DOMPurify 净化

## API

任何 OpenAI 兼容客户端把 `base_url` 指过来即可：

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "sonnet", "messages": [{"role": "user", "content": "你好"}], "stream": true}'
```

- `GET /v1/models` — 全部模型，`provider/model` 格式，别名：`fable` `opus` `sonnet` `haiku` `gemini-pro` `flash`
- 支持 OpenAI 标准 `reasoning_effort` 字段：claude（默认 `medium`）、codex 生效；agy 的深度编码在模型变体名里（`-low/-medium/-high`）
- `GET /admin/health`、`GET /auth/status` — provider 健康与登录指引
- Swagger 文档：`/docs`

## 配置

用户配置在 `~/.llm-bridge/config.yaml`（默认配置随包内置）：

```yaml
server:
  host: "127.0.0.1"
  port: 8787
  api_key: ""            # 设置后要求 Authorization: Bearer <key>

providers:
  claude:
    enabled: true
    api_key: ""          # 可选：用免费 Models API 动态拉模型列表（绝不用于推理）
  codex:
    enabled: true
    ignore_user_config: true   # 隔离 ~/.codex 技能配置（否则每请求多 ~2 万 token）
  agy:
    enabled: true
    cli_path: "agy"

routing:
  default_model: "claude/claude-sonnet-5"
```

### 局域网访问（自己的多台设备）

```bash
LLM_BRIDGE_API_KEY=$(openssl rand -hex 24) uv run llm-bridge --host 0.0.0.0
```

其他设备打开 `http://<内网IP>:8787`，在设置里填入 key。**开放监听前必须先设 key**，否则同网段任何人都能烧你的订阅额度。注意：流量是明文 HTTP，只在可信网络内使用，绝不要端口转发到公网。给自己设备以外的人共享，大概率违反订阅条款——这些是个人账号。

## 限制

- **仅聊天。** 不支持 `tools` 和多模态内容。采样参数（`temperature`、`max_tokens` 等）接受但不转发——CLI harness 不暴露它们。
- CLI 子进程延迟：每请求约 3–8 秒（codex/agy），Claude 经 Agent SDK 相近。
- 并发有限（每 provider 同时 2 个请求）；这是个人网关，不是 serving 基础设施。
- agy 输出为纯文本：块级流式、无 token 统计。

## 开发

```bash
uv sync
uv run llm-bridge --debug
uv run ruff check src scripts
uv run python scripts/test_providers.py     # 冒烟测试（需服务器运行 + CLI 已登录）
```

架构不变量（子进程生命周期规则、合规红线）见 [CONTRIBUTING.md](CONTRIBUTING.md)；AI 助手开发上下文见 [CLAUDE.md](CLAUDE.md)。

## License

[MIT](LICENSE)
