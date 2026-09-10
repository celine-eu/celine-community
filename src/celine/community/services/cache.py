"""Small in-process TTL cache for REC aggregate responses."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

from celine.community.settings import settings

T = TypeVar("T")


@dataclass
class _Entry[T]:
    value: T
    expires_at: float


class AggregateCache:
    """TTL cache with per-key locks to avoid duplicate downstream requests."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[tuple, _Entry] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    async def get_or_set(
        self,
        key: tuple,
        producer: Callable[[], Awaitable[T]],
    ) -> tuple[T, bool]:
        now = monotonic()
        cached = self._values.get(key)
        if cached and cached.expires_at > now:
            return cached.value, True

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = monotonic()
            cached = self._values.get(key)
            if cached and cached.expires_at > now:
                return cached.value, True
            value = await producer()
            self._values[key] = _Entry(value=value, expires_at=now + self.ttl_seconds)
            return value, False

    def clear(self) -> None:
        self._values.clear()
        self._locks.clear()


aggregate_cache = AggregateCache(settings.aggregate_cache_ttl_seconds)
