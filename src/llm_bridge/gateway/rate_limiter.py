"""In-memory token-bucket rate limiter."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """Token bucket for rate limiting."""
    capacity: float
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)

    def try_consume(self, now: float) -> bool:
        """Refill and try to consume one token. Returns True if allowed."""
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * (self.capacity / 60.0))
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-provider in-memory rate limiter using token buckets."""

    def __init__(self, default_rpm: int = 60) -> None:
        self.default_rpm = default_rpm
        self._buckets: dict[str, _Bucket] = {}
        self._provider_rpms: dict[str, int] = {}

    def configure_provider(self, provider: str, rpm: int) -> None:
        self._provider_rpms[provider] = rpm

    def check(self, provider: str) -> bool:
        """Check if a request to this provider is allowed."""
        now = time.monotonic()
        if provider not in self._buckets:
            rpm = self._provider_rpms.get(provider, self.default_rpm)
            self._buckets[provider] = _Bucket(capacity=float(rpm), tokens=float(rpm))

        return self._buckets[provider].try_consume(now)

    def reset(self, provider: str | None = None) -> None:
        """Reset rate limiter for a provider or all providers."""
        if provider:
            self._buckets.pop(provider, None)
        else:
            self._buckets.clear()
