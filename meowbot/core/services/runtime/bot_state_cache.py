from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass


log = logging.getLogger("meowbot")


@dataclass
class _LockState:
    owner: str
    expires_at_ms: int


class BotStateCache:
    """Process-local runtime state cache for entry cursors and locks."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.locks: dict[str, _LockState] = {}

    def preload(self, values: dict[str, int]) -> None:
        self.values.update({str(key): int(value) for key, value in values.items()})
        log.info("[cache] bot_state preload count=%s", len(values))

    def get_int(self, key: str, default: int = 0) -> int:
        normalized_key = str(key)
        if normalized_key not in self.values:
            log.debug("[cache] bot_state miss key=%s", normalized_key)
            return int(default)
        log.debug("[cache] bot_state hit key=%s", normalized_key)
        return int(self.values[normalized_key])

    def set_int(self, key: str, value: int) -> None:
        self.values[str(key)] = int(value)

    def acquire_lock(self, *, key: str, owner: str, ttl_ms: int = 120_000) -> bool:
        now_ms = int(time.time() * 1000)
        normalized_key = str(key)
        current = self.locks.get(normalized_key)
        if current is not None and current.expires_at_ms >= now_ms and current.owner != owner:
            return False

        self.locks[normalized_key] = _LockState(
            owner=str(owner),
            expires_at_ms=now_ms + max(int(ttl_ms), 1),
        )
        return True

    def release_lock(self, *, key: str, owner: str) -> None:
        normalized_key = str(key)
        current = self.locks.get(normalized_key)
        if current is None or current.owner != str(owner):
            return
        self.locks.pop(normalized_key, None)


class CacheBackedBotStateRepositoryAsync:
    """Async bot-state API backed by memory reads and optional Mongo writes."""

    def __init__(
        self,
        *,
        cache: BotStateCache,
        persistence_repo=None,
        persist_writes: bool = True,
        max_pending_persist_tasks: int = 100,
    ) -> None:
        self.cache = cache
        self.persistence_repo = persistence_repo
        self.persist_writes = bool(persist_writes)
        self.max_pending_persist_tasks = int(max_pending_persist_tasks)
        self._pending_persist_tasks: set[asyncio.Task] = set()

    async def preload_from_persistence(self) -> None:
        if self.persistence_repo is None:
            return
        try:
            rows = await self.persistence_repo.list_ints()
        except Exception as exc:
            log.warning("[cache] bot_state preload skipped error=%s:%s", type(exc).__name__, exc)
            return
        self.cache.preload(rows)

    async def get_int(self, key: str, default: int = 0) -> int:
        return self.cache.get_int(key, default=default)

    async def set_int(self, key: str, value: int) -> None:
        self.cache.set_int(key, value)
        await self._schedule_set_int(key, value)

    async def acquire_lock(self, *, key: str, owner: str, ttl_ms: int = 120_000) -> bool:
        acquired = self.cache.acquire_lock(key=key, owner=owner, ttl_ms=ttl_ms)
        log.debug("[cache] bot_state lock key=%s owner=%s acquired=%s", key, owner, acquired)
        return acquired

    async def release_lock(self, *, key: str, owner: str) -> None:
        self.cache.release_lock(key=key, owner=owner)

    async def _schedule_set_int(self, key: str, value: int) -> None:
        if not self.persist_writes or self.persistence_repo is None:
            return
        if len(self._pending_persist_tasks) >= self.max_pending_persist_tasks:
            log.warning(
                "[cache] bot_state Mongo persistence skipped key=%s pending_tasks=%s",
                key,
                len(self._pending_persist_tasks),
            )
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            await self._persist_set_int(key, value)
            return

        task = asyncio.create_task(self._persist_set_int(key, value))
        self._pending_persist_tasks.add(task)
        task.add_done_callback(self._pending_persist_tasks.discard)

    async def _persist_set_int(self, key: str, value: int) -> None:
        try:
            await self.persistence_repo.set_int(key, value)
        except Exception as exc:
            log.warning("[cache] bot_state Mongo persistence failed key=%s error=%s:%s", key, type(exc).__name__, exc)


class InMemoryBotStateRepository:
    """Sync bot-state adapter for the legacy online worker."""

    def __init__(self, cache: BotStateCache | None = None) -> None:
        self.cache = cache or BotStateCache()

    def get_int(self, key: str, default: int = 0) -> int:
        return self.cache.get_int(key, default=default)

    def set_int(self, key: str, value: int) -> None:
        self.cache.set_int(key, value)
