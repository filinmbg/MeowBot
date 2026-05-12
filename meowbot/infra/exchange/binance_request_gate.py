from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import httpx


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class BinanceGateConfig:
    max_weight_per_minute: int = 2400
    max_concurrent_requests: int = 4
    retry_attempts: int = 5
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 15.0


class BinanceRequestGate:
    """
    Один централізований gate для всіх REST-запитів до Binance.

    - обмежує конкурентність
    - обмежує REQUEST_WEIGHT у вікні 60 секунд
    - поважає Retry-After / retryAfter
    - після 418/429 ставить глобальну паузу
    """

    def __init__(self, config: BinanceGateConfig) -> None:
        self.config = config
        self._sem = asyncio.Semaphore(config.max_concurrent_requests)

        self._weight_window: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

        self._blocked_until_monotonic: float = 0.0
        self._last_block_reason: str | None = None
        self._last_error: str | None = None

    async def execute_json(
        self,
        *,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        params: dict | None = None,
        params_factory: Callable[[], dict[str, Any]] | None = None,
        weight: int = 1,
    ) -> dict | list:
        params = params or {}
        last_exc: Exception | None = None

        for attempt in range(1, self.config.retry_attempts + 1):
            request_params = dict(params)
            request_started_at_ms = int(time.time() * 1000)
            request_timestamp_ms: int | None = None
            request_age_ms: int | None = None
            try:
                await self._wait_for_slot(weight)

                async with self._sem:
                    request_started_at_ms = int(time.time() * 1000)
                    request_params = params_factory() if callable(params_factory) else dict(params)
                    request_timestamp_ms = self._extract_request_timestamp(request_params)
                    if request_timestamp_ms is not None:
                        request_age_ms = max(0, request_started_at_ms - request_timestamp_ms)
                    response = await client.request(method, path, params=request_params)
                    await self._handle_response_limits(response)

                    response.raise_for_status()
                    self._last_error = None
                    return response.json()

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None

                if status in (418, 429):
                    delay = self._extract_retry_after_seconds(exc.response)
                    if delay is None:
                        delay = self._retry_delay(attempt)

                    await self._block_for(delay, reason=f"http_status_{status}")
                    self._last_error = f"{status} on {path}"

                    log.warning(
                        "[binance-gate] status=%s path=%s attempt=%s/%s sleep=%.2fs",
                        status,
                        path,
                        attempt,
                        self.config.retry_attempts,
                        delay,
                    )

                    if attempt >= self.config.retry_attempts:
                        break

                    await asyncio.sleep(delay)
                    continue

                if status is not None and 400 <= status < 500:
                    self._last_error = f"{status} on {path}"
                    if self._is_expected_algo_cancel_not_found(
                        method=method,
                        path=path,
                        response=exc.response,
                    ):
                        log.info(
                            "[binance-gate] idempotent cancel not found status=%s path=%s params=%s response=%s",
                            status,
                            path,
                            self._sanitize_params(request_params),
                            self._safe_response_text(exc.response),
                        )
                    else:
                        error_code = self._extract_binance_error_code(exc.response)
                        log.warning(
                            "[binance-gate] non-retriable status=%s path=%s params=%s response=%s code=%s request_age_ms=%s timestamp_ms=%s local_time_ms=%s decision=%s",
                            status,
                            path,
                            self._sanitize_params(request_params),
                            self._safe_response_text(exc.response),
                            error_code,
                            request_age_ms,
                            request_timestamp_ms,
                            request_started_at_ms,
                            "accept" if error_code != -1021 else "retry_or_raise",
                        )
                    raise

                delay = self._retry_delay(attempt)
                self._last_error = f"{status} on {path}"
                log.warning(
                    "[binance-gate] retriable http status=%s path=%s attempt=%s/%s sleep=%.2fs",
                    status,
                    path,
                    attempt,
                    self.config.retry_attempts,
                    delay,
                )
                if attempt >= self.config.retry_attempts:
                    break
                await asyncio.sleep(delay)

            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                delay = self._retry_delay(attempt)
                self._last_error = f"{type(exc).__name__} on {path}"

                log.warning(
                    "[binance-gate] network error=%s path=%s attempt=%s/%s sleep=%.2fs",
                    type(exc).__name__,
                    path,
                    attempt,
                    self.config.retry_attempts,
                    delay,
                )

                if attempt >= self.config.retry_attempts:
                    break

                await asyncio.sleep(delay)

            except httpx.HTTPError as exc:
                last_exc = exc
                delay = self._retry_delay(attempt)
                self._last_error = f"{type(exc).__name__} on {path}"

                log.warning(
                    "[binance-gate] generic http error=%s path=%s attempt=%s/%s sleep=%.2fs",
                    type(exc).__name__,
                    path,
                    attempt,
                    self.config.retry_attempts,
                    delay,
                )

                if attempt >= self.config.retry_attempts:
                    break

                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def _wait_for_slot(self, weight: int) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()

                if now < self._blocked_until_monotonic:
                    sleep_for = self._blocked_until_monotonic - now
                else:
                    self._cleanup_old(now)
                    used_weight = sum(w for _, w in self._weight_window)

                    if used_weight + weight <= self.config.max_weight_per_minute:
                        self._weight_window.append((now, weight))
                        return

                    oldest_ts, _ = self._weight_window[0]
                    sleep_for = max(0.05, 60.0 - (now - oldest_ts))

            await asyncio.sleep(sleep_for)

    async def _handle_response_limits(self, response: httpx.Response) -> None:
        retry_after = self._extract_retry_after_seconds(response)
        if retry_after is not None and response.status_code in (418, 429):
            await self._block_for(retry_after, reason=f"http_status_{response.status_code}")

    async def _block_for(self, seconds: float, *, reason: str) -> None:
        seconds = max(0.0, float(seconds))
        async with self._lock:
            until = time.monotonic() + seconds
            if until > self._blocked_until_monotonic:
                self._blocked_until_monotonic = until
                self._last_block_reason = reason

    def get_status(self) -> dict:
        now = time.monotonic()
        blocked_for = max(0.0, self._blocked_until_monotonic - now)
        used_weight = sum(w for ts, w in self._weight_window if (now - ts) < 60.0)

        return {
            "blocked_for_seconds": blocked_for,
            "blocked": blocked_for > 0,
            "last_block_reason": self._last_block_reason,
            "last_error": self._last_error,
            "used_weight_last_minute": used_weight,
            "max_weight_per_minute": self.config.max_weight_per_minute,
            "max_concurrent_requests": self.config.max_concurrent_requests,
        }

    def _cleanup_old(self, now: float) -> None:
        while self._weight_window and (now - self._weight_window[0][0]) >= 60.0:
            self._weight_window.popleft()

    def _retry_delay(self, attempt: int) -> float:
        delay = self.config.retry_base_delay_seconds * (2 ** (attempt - 1))
        return min(delay, self.config.retry_max_delay_seconds)

    def _extract_retry_after_seconds(self, response: httpx.Response | None) -> float | None:
        if response is None:
            return None

        header_value = response.headers.get("Retry-After")
        if header_value:
            try:
                return float(header_value)
            except Exception:
                pass

        try:
            data = response.json()
            retry_after = data.get("retryAfter")
            if retry_after is not None:
                return float(retry_after)
        except Exception:
            pass

        return None

    def _sanitize_params(self, params: dict | None) -> dict:
        sanitized = dict(params or {})
        for key in list(sanitized.keys()):
            if str(key).lower() in {"signature"}:
                sanitized[key] = "<redacted>"
        return sanitized

    def _extract_request_timestamp(self, params: dict[str, Any] | None) -> int | None:
        if not isinstance(params, dict):
            return None
        try:
            raw = params.get("timestamp")
            if raw is None:
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _is_expected_algo_cancel_not_found(
        self,
        *,
        method: str,
        path: str,
        response: httpx.Response | None,
    ) -> bool:
        if str(method).upper() != "DELETE":
            return False
        if str(path) != "/fapi/v1/algoOrder":
            return False
        return self._extract_binance_error_code(response) == -2011

    def _extract_binance_error_code(self, response: httpx.Response | None) -> int | None:
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return int(payload.get("code"))
        except Exception:
            return None

    def _safe_response_text(self, response: httpx.Response | None, *, limit: int = 500) -> str | None:
        if response is None:
            return None
        try:
            text = str(response.text or "")
        except Exception:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
