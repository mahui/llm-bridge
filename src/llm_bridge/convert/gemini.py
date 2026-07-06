"""OpenAI <-> Gemini / Cloud Code Assist format conversion."""

from __future__ import annotations

from llm_bridge.models import (
    ChatCompletionChoice,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    UsageInfo,
)


def to_gemini_request(request: ChatCompletionRequest) -> dict:
    """Convert OpenAI ChatCompletionRequest to Gemini generateContent format."""
    contents: list[dict] = []
    system_parts: list[dict] = []

    for msg in request.messages:
        if msg.role == "system":
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            system_parts.append({"text": text})
            continue

        gemini_role = "user" if msg.role == "user" else "model"
        parts = _convert_content_to_parts(msg)
        contents.append({"role": gemini_role, "parts": parts})

    result: dict = {"contents": contents}

    if system_parts:
        result["systemInstruction"] = {"parts": system_parts}

    # Generation config
    gen_config: dict = {}
    if request.max_tokens is not None:
        gen_config["maxOutputTokens"] = request.max_tokens
    if request.temperature is not None:
        gen_config["temperature"] = request.temperature
    if request.top_p is not None:
        gen_config["topP"] = request.top_p
    if request.stop:
        stops = request.stop if isinstance(request.stop, list) else [request.stop]
        gen_config["stopSequences"] = stops

    if gen_config:
        result["generationConfig"] = gen_config

    return result


def _convert_content_to_parts(msg: ChatMessage) -> list[dict]:
    """Convert message content to Gemini parts format."""
    content = msg.content
    if isinstance(content, str):
        return [{"text": content}]

    if isinstance(content, list):
        parts = []
        for item in content:
            if item.get("type") == "text":
                parts.append({"text": item["text"]})
            elif item.get("type") == "image_url":
                url = item.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    # Base64 inline image
                    mime, b64 = url.split(";base64,", 1)
                    mime = mime.split(":", 1)[1]
                    parts.append({
                        "inlineData": {"mimeType": mime, "data": b64}
                    })
                else:
                    parts.append({
                        "fileData": {"mimeType": "image/jpeg", "fileUri": url}
                    })
        return parts

    return [{"text": str(content) if content else ""}]


def from_gemini_response(data: dict, model: str = "") -> ChatCompletionResponse:
    """Convert Gemini generateContent response to OpenAI format."""
    candidates = data.get("candidates", [])
    if not candidates:
        return ChatCompletionResponse(
            model=model,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(content=""),
                    finish_reason="stop",
                )
            ],
        )

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])

    text_parts = []
    for part in parts:
        if "text" in part:
            text_parts.append(part["text"])

    text = "".join(text_parts)

    finish_reason_map = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "stop",
        "RECITATION": "stop",
    }
    finish_reason = finish_reason_map.get(
        candidate.get("finishReason", "STOP"), "stop"
    )

    usage_meta = data.get("usageMetadata", {})
    usage = UsageInfo(
        prompt_tokens=usage_meta.get("promptTokenCount", 0),
        completion_tokens=usage_meta.get("candidatesTokenCount", 0),
        total_tokens=usage_meta.get("totalTokenCount", 0),
    )

    return ChatCompletionResponse(
        model=model,
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessage(content=text),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )
