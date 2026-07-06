# [开源] LLM-Bridge：把 Claude Code / Codex / Antigravity 三个订阅统一成一个 OpenAI 兼容 API

同时订阅了 Claude、ChatGPT 和 Google Antigravity 之后，我发现额度散在三个 CLI 里，每个交互方式还都不一样。于是写了这个本地网关：**把三家的 CLI 包成一个 OpenAI 兼容端点**，自带一个聊天 UI，个人多设备用。

GitHub：https://github.com/mahui/llm-bridge （MIT）

![聊天界面](https://raw.githubusercontent.com/mahui/llm-bridge/main/docs/screenshot-chat.png)

## 它做什么

- 一个 `base_url`，通吃三家模型：`claude/claude-sonnet-5`、`codex/gpt-5.5`、`agy/claude-sonnet-4.6-thinking`……任何 OpenAI SDK 客户端改一行就能接
- 鉴权完全复用各家 CLI 自己的登录态——**不碰、不提取、不重放任何 OAuth token**。今年上半年 Anthropic 封杀第三方 token 复用那波大家都见过了，这个项目从设计上就只走官方 harness（claude-agent-sdk / `codex exec` / `agy -p`），被封的路一行代码都没有
- 模型列表尽量动态：codex 读 CLI 自己的缓存，agy 直接 `agy models`，不用追着上游改版本号
- 内置 Web UI：多会话并发流式、provider 信号色（一眼看出回复出自哪家）、日夜主题、设置页（API key / system prompt / 推理深度）
- API 视图带 playground：选个模型直接在页面里试跑，cURL/Python/JS 示例代码跟着你选的模型自动生成

![API 视图](https://raw.githubusercontent.com/mahui/llm-bridge/main/docs/screenshot-api.png)

## 一些实现上有意思的点

- Codex 默认加了 `--ignore-user-config`：不加的话你全局装的技能/插件会被灌进每个请求——我实测一句 "hello world" 烧了 2.8 万 input token、等 37 秒；隔离后 1 万 token、7 秒
- 透传了 OpenAI 标准的 `reasoning_effort` 字段：claude 默认压到 medium（SDK 默认 high，闲聊也烧深度推理的额度）
- 子进程流式的坑都踩过了：客户端断连的僵尸进程、stderr 管道写满死锁、截断误报 `finish_reason=stop`，修复都在提交历史里

## 限制（先自己交代了）

- **仅聊天**：不支持 tool calling / 多模态，`temperature` 这类采样参数接受但不转发（CLI harness 不暴露）
- 延迟比直连 API 高：每请求 3-8 秒（CLI 子进程的固有成本）
- 单用户定位：并发上限每 provider 2 个请求，没有多租户
- Claude 的 headless 用量走每月 Agent SDK credit，不是无限池——薅羊毛请理性
- 给自己的手机/iPad/其他电脑用没问题（LAN 模式 + API key），共享给别人大概率违反订阅条款，别这么干

## 安装

```bash
uv tool install git+https://github.com/mahui/llm-bridge
llm-bridge
# 打开 http://127.0.0.1:8787
```

前置：Python 3.12+，至少一个已登录的 CLI（`claude` / `codex login` / `agy`）。

技术栈：Python + FastAPI，前端是单文件 vanilla JS（无构建步骤）。欢迎 issue / PR，尤其欢迎给 convert 层补单测的（目前 tests/ 还是空的，惭愧）。
