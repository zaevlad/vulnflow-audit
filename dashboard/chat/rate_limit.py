"""Async token-bucket rate limiter for external tool calls.

Used by the chat agent to honor the per-tool quotas advertised in
``conf.yaml`` (e.g. HornetMCP 10 req/min, Solodit 20 req/60s) without
having to wait for the upstream service to return HTTP 429.

A bucket is configured per tool name and refills at a steady rate.
``acquire()`` consumes one token and returns ``(True, 0.0)`` if it
succeeded, or ``(False, retry_after)`` after ``timeout`` seconds of
waiting. The agent decides whether to surface a "rate_limited" tool
result to the model or to back off and retry.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class BucketSpec:
    capacity: float
    refill_per_second: float


class _TokenBucket:
    __slots__ = ("spec", "tokens", "updated_at", "lock")

    def __init__(self, spec: BucketSpec) -> None:
        self.spec = spec
        self.tokens = float(spec.capacity)
        self.updated_at = time.monotonic()
        self.lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.spec.capacity, self.tokens + elapsed * self.spec.refill_per_second)
        self.updated_at = now

    async def acquire(self, timeout: float) -> tuple[bool, float]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            async with self.lock:
                now = time.monotonic()
                self._refill(now)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True, 0.0
                # Time until at least one token will be available.
                missing = 1.0 - self.tokens
                wait = missing / self.spec.refill_per_second if self.spec.refill_per_second > 0 else float("inf")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, max(0.0, wait)
            await asyncio.sleep(min(wait, remaining, 0.5))


class RateLimiterRegistry:
    """Manages a bucket per tool name, configured from external_tools data."""

    def __init__(self) -> None:
        self._buckets: dict[str, _TokenBucket] = {}

    def configure_from_tools(self, tools: list[dict[str, Any]]) -> None:
        for tool in tools:
            name = str(tool.get("name", "")).strip()
            if not name:
                continue
            spec = _bucket_spec_from_config(tool.get("rate_limit"))
            if spec is None:
                continue
            self._buckets[name] = _TokenBucket(spec)

    def has_bucket(self, tool_name: str) -> bool:
        # Buckets are keyed by tool name (e.g. "hornetmcp"); endpoint names
        # like "hornetmcp_search" are translated by :meth:`acquire`.
        return any(tool_name == n or tool_name.startswith(f"{n}_") for n in self._buckets)

    async def acquire(
        self,
        endpoint_function_name: str,
        *,
        timeout: float = 3.0,
    ) -> tuple[bool, float, str]:
        """Acquire a token for an endpoint, returning (ok, retry_after, tool_name)."""
        tool_name = self._tool_name_for(endpoint_function_name)
        if not tool_name:
            return True, 0.0, ""
        bucket = self._buckets.get(tool_name)
        if bucket is None:
            return True, 0.0, tool_name
        ok, wait = await bucket.acquire(timeout=timeout)
        return ok, wait, tool_name

    def _tool_name_for(self, endpoint_function_name: str) -> str:
        # function name shape: "<tool>_<endpoint>"
        if endpoint_function_name in self._buckets:
            return endpoint_function_name
        for name in self._buckets:
            if endpoint_function_name.startswith(f"{name}_"):
                return name
        return ""


def _bucket_spec_from_config(config: Any) -> BucketSpec | None:
    if not isinstance(config, dict):
        return None

    capacity = None
    refill = None

    per_minute = config.get("per_minute")
    per_second = config.get("per_second")
    burst = config.get("burst")

    if isinstance(per_minute, (int, float)) and per_minute > 0:
        refill = float(per_minute) / 60.0
        capacity = float(burst) if isinstance(burst, (int, float)) and burst > 0 else float(per_minute)
    elif isinstance(per_second, (int, float)) and per_second > 0:
        refill = float(per_second)
        capacity = float(burst) if isinstance(burst, (int, float)) and burst > 0 else float(per_second)

    if capacity is None or refill is None:
        return None
    return BucketSpec(capacity=capacity, refill_per_second=refill)
