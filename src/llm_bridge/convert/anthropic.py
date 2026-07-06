"""OpenAI <-> Anthropic Messages format conversion."""

from __future__ import annotations

from llm_bridge.models import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionResponse,
    ChatMessage,
    UsageInfo,
)


def to_anthropic_messages(
    messages: list[ChatMessage],
) -> tuple[str | None, list[dict]]:
    """Convert OpenAI messages to Anthropic format.

    Returns:
        (system_prompt, anthropic_messages)
    """
    system_prompt: str | None = None
    anthropic_msgs: list[dict] = []

    for msg in messages:
        if msg.role == "system":
            # Anthropic uses a top-level system parameter
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            if system_prompt:
                system_prompt += "\n\n" + text
            else:
                system_prompt = text
            continue

        content = msg.content
        if isinstance(content, str):
            anthropic_msgs.append({"role": msg.role, "content": content})
        elif isinstance(content, list):
            # Multi-modal content blocks
            blocks = []
            for item in content:
                if item.get("type") == "text":
                    blocks.append({"type": "text", "text": item["text"]})
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    blocks.append({
                        "type": "image",
                        "source": {"type": "url", "url": url},
                    })
            anthropic_msgs.append({"role": msg.role, "content": blocks})
        else:
            anthropic_msgs.append({"role": msg.role, "content": content or ""})

    return system_prompt, anthropic_msgs


def from_anthropic_response(data: dict, model: str = "") -> ChatCompletionResponse:
    """Convert an Anthropic Messages API response to OpenAI format."""
    content_blocks = data.get("content", [])
    text_parts = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block["text"])
        elif isinstance(block, str):
            text_parts.append(block)

    text = "".join(text_parts)

    usage_data = data.get("usage", {})
    usage = UsageInfo(
        prompt_tokens=usage_data.get("input_tokens", 0),
        completion_tokens=usage_data.get("output_tokens", 0),
        total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
    )

    stop_reason = data.get("stop_reason", "stop")
    finish_reason_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
    }

    return ChatCompletionResponse(
        model=model or data.get("model", ""),
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessage(content=text),
                finish_reason=finish_reason_map.get(stop_reason, "stop"),
            )
        ],
        usage=usage,
    )


def format_messages_as_prompt(messages: list[ChatMessage]) -> str:
    """Format OpenAI messages as a single prompt string for Claude CLI."""
    parts = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if msg.role == "system":
            parts.append(f"[System]\n{content}")
        elif msg.role == "user":
            parts.append(content)
        elif msg.role == "assistant":
            parts.append(f"[Previous Assistant Response]\n{content}")
    return "\n\n".join(parts)
