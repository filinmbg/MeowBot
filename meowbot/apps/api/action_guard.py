from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse


log = logging.getLogger("meowbot")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCK_MESSAGE = "Операція вже виконується. Зачекайте кілька секунд."
COOLDOWN_MESSAGE = "Операцію виконано нещодавно. Зачекайте кілька секунд."


@dataclass
class CachedActionResponse:
    status_code: int
    body: bytes
    media_type: str | None
    headers: dict[str, str]
    created_at: float


class ApiActionGuard:
    def __init__(
        self,
        *,
        cooldown_seconds: float = 3.0,
        idempotency_ttl_seconds: float = 10.0,
        max_cached_responses: int = 512,
        max_cached_body_bytes: int = 64_000,
    ) -> None:
        self.cooldown_seconds = max(float(cooldown_seconds), 0.0)
        self.idempotency_ttl_seconds = max(float(idempotency_ttl_seconds), self.cooldown_seconds)
        self.max_cached_responses = max(int(max_cached_responses), 1)
        self.max_cached_body_bytes = max(int(max_cached_body_bytes), 0)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_action_at: dict[str, float] = {}
        self._cached_responses: OrderedDict[str, CachedActionResponse] = OrderedDict()
        self._guard_lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "ApiActionGuard":
        return cls(
            cooldown_seconds=_float_env("API_ACTION_COOLDOWN_SECONDS", 3.0),
            idempotency_ttl_seconds=_float_env("API_ACTION_IDEMPOTENCY_TTL_SECONDS", 10.0),
            max_cached_responses=int(_float_env("API_ACTION_CACHE_SIZE", 512)),
            max_cached_body_bytes=int(_float_env("API_ACTION_MAX_CACHED_BODY_BYTES", 64_000)),
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method.upper() not in MUTATING_METHODS:
            return await call_next(request)

        started_at = time.monotonic()
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        body = await request.body()
        user_id = self._extract_user_id(request=request, body=body)
        action = self._action_name(request)
        action_key = self._action_key(user_id=user_id, action=action)
        fingerprint = self._fingerprint(request=request, body=body)
        cache_key = f"{action_key}:{fingerprint}"

        await self._cleanup_expired_cache(now=started_at)

        cached = await self._get_cached_response(cache_key, now=started_at)
        if cached is not None:
            log.info(
                "[api-action-guard] idempotent replay user_id=%s action=%s request_id=%s status=%s",
                user_id,
                action,
                request_id,
                cached.status_code,
            )
            return self._response_from_cache(cached, request_id=request_id, replay=True)

        lock = await self._lock_for(action_key)
        if lock.locked():
            log.info(
                "[api-action-guard] concurrent action blocked user_id=%s action=%s request_id=%s",
                user_id,
                action,
                request_id,
            )
            return self._json_error(
                status_code=409,
                message=LOCK_MESSAGE,
                user_id=user_id,
                action=action,
                request_id=request_id,
            )

        async with lock:
            now = time.monotonic()
            cached = await self._get_cached_response(cache_key, now=now)
            if cached is not None:
                log.info(
                    "[api-action-guard] idempotent replay after lock user_id=%s action=%s request_id=%s status=%s",
                    user_id,
                    action,
                    request_id,
                    cached.status_code,
                )
                return self._response_from_cache(cached, request_id=request_id, replay=True)

            last_action_at = self._last_action_at.get(action_key)
            cooldown_left = self._cooldown_left(last_action_at=last_action_at, now=now)
            if cooldown_left > 0:
                log.info(
                    "[api-action-guard] cooldown blocked user_id=%s action=%s cooldown_left=%.3f request_id=%s",
                    user_id,
                    action,
                    cooldown_left,
                    request_id,
                )
                return self._json_error(
                    status_code=429,
                    message=COOLDOWN_MESSAGE,
                    user_id=user_id,
                    action=action,
                    request_id=request_id,
                    cooldown_left=cooldown_left,
                )

            response = await call_next(request)
            captured = await self._capture_response(response)
            self._last_action_at[action_key] = time.monotonic()

            if (
                captured.status_code < 500
                and self.max_cached_body_bytes > 0
                and len(captured.body) <= self.max_cached_body_bytes
            ):
                await self._store_cached_response(cache_key, captured)

            log.info(
                "[api-action-guard] action completed user_id=%s action=%s status=%s duration_ms=%s request_id=%s",
                user_id,
                action,
                captured.status_code,
                int((time.monotonic() - started_at) * 1000),
                request_id,
            )
            return self._response_from_cache(captured, request_id=request_id, replay=False)

    def _extract_user_id(self, *, request: Request, body: bytes) -> str:
        for key in ("user_id", "telegram_user_id", "telegram_id"):
            value = request.query_params.get(key)
            if value:
                return str(value)

        payload = self._json_body(body)
        if isinstance(payload, dict):
            for key in ("user_id", "telegram_user_id", "telegram_id", "id"):
                value = payload.get(key)
                if value not in {None, ""}:
                    return str(value)

        path_user = self._path_user_id(request.url.path)
        if path_user:
            return path_user

        client_host = getattr(request.client, "host", None) if request.client else None
        return f"anonymous:{client_host or 'unknown'}"

    @staticmethod
    def _json_body(body: bytes) -> Any:
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _path_user_id(path: str) -> str | None:
        for part in str(path or "").split("/"):
            if not part:
                continue
            try:
                uuid.UUID(part)
                return part
            except ValueError:
                continue
        return None

    @staticmethod
    def _action_name(request: Request) -> str:
        return f"{request.method.upper()} {request.url.path}"

    @staticmethod
    def _action_key(*, user_id: str, action: str) -> str:
        return f"{user_id}:{action}"

    @staticmethod
    def _fingerprint(*, request: Request, body: bytes) -> str:
        digest = hashlib.sha256()
        digest.update(request.method.upper().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(request.url.path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(request.url.query).encode("utf-8"))
        digest.update(b"\0")
        digest.update(body)
        return digest.hexdigest()

    async def _lock_for(self, action_key: str) -> asyncio.Lock:
        async with self._guard_lock:
            lock = self._locks.get(action_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[action_key] = lock
            return lock

    def _cooldown_left(self, *, last_action_at: float | None, now: float) -> float:
        if last_action_at is None or self.cooldown_seconds <= 0:
            return 0.0
        return max(self.cooldown_seconds - (now - last_action_at), 0.0)

    async def _get_cached_response(self, cache_key: str, *, now: float) -> CachedActionResponse | None:
        async with self._guard_lock:
            cached = self._cached_responses.get(cache_key)
            if cached is None:
                return None
            if now - cached.created_at > self.idempotency_ttl_seconds:
                self._cached_responses.pop(cache_key, None)
                return None
            self._cached_responses.move_to_end(cache_key)
            return cached

    async def _store_cached_response(self, cache_key: str, response: CachedActionResponse) -> None:
        async with self._guard_lock:
            self._cached_responses[cache_key] = response
            self._cached_responses.move_to_end(cache_key)
            while len(self._cached_responses) > self.max_cached_responses:
                self._cached_responses.popitem(last=False)

    async def _cleanup_expired_cache(self, *, now: float) -> None:
        async with self._guard_lock:
            expired = [
                key
                for key, cached in self._cached_responses.items()
                if now - cached.created_at > self.idempotency_ttl_seconds
            ]
            for key in expired:
                self._cached_responses.pop(key, None)

    async def _capture_response(self, response: Response) -> CachedActionResponse:
        body = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body += bytes(chunk)

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding"}
        }
        return CachedActionResponse(
            status_code=response.status_code,
            body=body,
            media_type=response.media_type,
            headers=headers,
            created_at=time.monotonic(),
        )

    @staticmethod
    def _response_from_cache(
        cached: CachedActionResponse,
        *,
        request_id: str,
        replay: bool,
    ) -> Response:
        headers = dict(cached.headers)
        headers["x-request-id"] = request_id
        if replay:
            headers["x-idempotent-replay"] = "true"
        return Response(
            content=cached.body,
            status_code=cached.status_code,
            headers=headers,
            media_type=cached.media_type,
        )

    @staticmethod
    def _json_error(
        *,
        status_code: int,
        message: str,
        user_id: str,
        action: str,
        request_id: str,
        cooldown_left: float | None = None,
    ) -> JSONResponse:
        payload: dict[str, Any] = {
            "detail": message,
            "user_id": user_id,
            "action": action,
            "request_id": request_id,
        }
        if cooldown_left is not None:
            payload["cooldown_left_seconds"] = round(float(cooldown_left), 3)
        return JSONResponse(
            status_code=status_code,
            content=payload,
            headers={"x-request-id": request_id},
        )


def install_action_guard(app) -> ApiActionGuard:
    guard = ApiActionGuard.from_env()
    app.state.action_guard = guard

    @app.middleware("http")
    async def action_guard_middleware(request: Request, call_next):
        return await guard.dispatch(request, call_next)

    log.info(
        "[api-action-guard] installed cooldown_seconds=%.3f idempotency_ttl_seconds=%.3f",
        guard.cooldown_seconds,
        guard.idempotency_ttl_seconds,
    )
    return guard


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)
