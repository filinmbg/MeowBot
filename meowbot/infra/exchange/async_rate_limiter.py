from __future__ import annotations

import asyncio
import time
from collections import deque


class AsyncSlidingWindowRateLimiter:
    """
    Простий async rate limiter:
    - не більше max_calls за period_seconds
    - додатково обмежує одночасність через semaphore
    """

    def __init__(
        self,
        *,
        max_calls: int,
        period_seconds: float,
        max_concurrent: int,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be > 0")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be > 0")
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")

        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()

    async def acquire(self) -> None:
        await self._semaphore.acquire()

        while True:
            async with self._lock:
                now = time.monotonic()

                while self._timestamps and (now - self._timestamps[0]) >= self.period_seconds:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.max_calls:
                    self._timestamps.append(now)
                    return

                sleep_for = self.period_seconds - (now - self._timestamps[0])
                sleep_for = max(sleep_for, 0.01)

            await asyncio.sleep(sleep_for)

    def release(self) -> None:
        self._semaphore.release()