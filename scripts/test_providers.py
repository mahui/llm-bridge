"""Test script for LLM-Bridge - tests all providers with streaming and non-streaming."""

from __future__ import annotations

import argparse
import sys
import time

from openai import OpenAI

BASE_URL = "http://127.0.0.1:8787/v1"
PROMPT = "Say 'hello world' and nothing else."

# One model per provider to test
TEST_MODELS = [
    ("claude", "claude/claude-haiku-4-5"),
    ("codex", "codex/gpt-5.4-mini"),
    ("agy", "agy/gemini-3.5-flash-low"),
]


def test_models_endpoint(client: OpenAI) -> bool:
    print("=" * 60)
    print("Testing GET /v1/models")
    print("=" * 60)
    try:
        models = client.models.list()
        print(f"  Found {len(models.data)} models:")
        for m in models.data:
            print(f"    - {m.id} (owned_by: {m.owned_by})")
        print("  ✅ PASS\n")
        return True
    except Exception as e:
        print(f"  ❌ FAIL: {e}\n")
        return False


def test_non_streaming(client: OpenAI, provider: str, model: str) -> bool:
    print(f"  [non-streaming] {model}")
    try:
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            stream=False,
        )
        elapsed = time.monotonic() - t0
        content = resp.choices[0].message.content or ""
        usage = resp.usage
        print(f"    Response: {content!r}")
        print(f"    Usage: {usage.prompt_tokens}in/{usage.completion_tokens}out")
        print(f"    Time: {elapsed:.1f}s")
        if not content:
            print("    ❌ FAIL: empty response")
            return False
        print("    ✅ PASS")
        return True
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "exhausted" in msg.lower():
            print(f"    ⚠️  RATE LIMITED (not a code bug): {_short_err(msg)}")
        elif "401" in msg or "scope" in msg.lower():
            print(f"    ⚠️  AUTH SCOPE ISSUE: {_short_err(msg)}")
        elif "403" in msg or "permission" in msg.lower():
            print(f"    ⚠️  PERMISSION DENIED: {_short_err(msg)}")
        else:
            print(f"    ❌ FAIL: {_short_err(msg)}")
        return False


def _short_err(msg: str) -> str:
    """Shorten long error messages."""
    if len(msg) > 120:
        return msg[:120] + "..."
    return msg


def test_streaming(client: OpenAI, provider: str, model: str) -> bool:
    print(f"  [streaming] {model}")
    try:
        t0 = time.monotonic()
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            stream=True,
        )
        chunks = []
        content_parts = []
        for chunk in stream:
            chunks.append(chunk)
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                content_parts.append(delta.content)
        elapsed = time.monotonic() - t0
        content = "".join(content_parts)
        print(f"    Response: {content!r}")
        print(f"    Chunks: {len(chunks)}")
        print(f"    Time: {elapsed:.1f}s")
        if not content:
            print("    ❌ FAIL: empty response (0 content chunks)")
            return False
        print("    ✅ PASS")
        return True
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "exhausted" in msg.lower():
            print(f"    ⚠️  RATE LIMITED (not a code bug): {_short_err(msg)}")
        elif "401" in msg or "scope" in msg.lower():
            print(f"    ⚠️  AUTH SCOPE ISSUE: {_short_err(msg)}")
        elif "403" in msg or "permission" in msg.lower():
            print(f"    ⚠️  PERMISSION DENIED: {_short_err(msg)}")
        else:
            print(f"    ❌ FAIL: {_short_err(msg)}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test LLM-Bridge providers")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--providers", nargs="*", help="Specific providers to test")
    parser.add_argument("--stream-only", action="store_true")
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    client = OpenAI(base_url=args.base_url, api_key="unused")

    results: dict[str, bool] = {}

    # Test models endpoint
    results["models"] = test_models_endpoint(client)

    # Test each provider
    for provider, model in TEST_MODELS:
        if args.providers and provider not in args.providers:
            continue

        print("=" * 60)
        print(f"Testing provider: {provider}")
        print("=" * 60)

        if not args.stream_only:
            key = f"{provider}/non-stream"
            results[key] = test_non_streaming(client, provider, model)

        if not args.no_stream:
            key = f"{provider}/stream"
            results[key] = test_streaming(client, provider, model)

        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
    print(f"\n  {passed}/{total} passed")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
