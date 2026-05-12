from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from meowbot.core.domain.types import Bar
from meowbot.infra.exchange.binance_request_gate import (
    BinanceGateConfig,
    BinanceRequestGate,
)


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class BinanceFuturesAsyncConfig:
    base_url: str = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")
    timeout_seconds: float = float(os.getenv("BINANCE_HTTP_TIMEOUT_SECONDS", "15"))

    max_weight_per_minute: int = int(os.getenv("BINANCE_MAX_WEIGHT_PER_MINUTE", "600"))
    max_concurrent_requests: int = int(os.getenv("BINANCE_MAX_CONCURRENT_REQUESTS", "2"))

    retry_attempts: int = int(os.getenv("BINANCE_HTTP_RETRY_ATTEMPTS", "5"))
    retry_base_delay_seconds: float = float(os.getenv("BINANCE_HTTP_RETRY_BASE_DELAY_SECONDS", "1.0"))
    retry_max_delay_seconds: float = float(os.getenv("BINANCE_HTTP_RETRY_MAX_DELAY_SECONDS", "15.0"))


class BinanceFuturesMarketDataAsync:
    """
    Всі REST-запити до Binance йдуть через один gate.
    """

    def __init__(self, config: BinanceFuturesAsyncConfig):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            limits=httpx.Limits(
                max_connections=self.config.max_concurrent_requests,
                max_keepalive_connections=self.config.max_concurrent_requests,
            ),
        )
        self._gate = BinanceRequestGate(
            BinanceGateConfig(
                max_weight_per_minute=self.config.max_weight_per_minute,
                max_concurrent_requests=self.config.max_concurrent_requests,
                retry_attempts=self.config.retry_attempts,
                retry_base_delay_seconds=self.config.retry_base_delay_seconds,
                retry_max_delay_seconds=self.config.retry_max_delay_seconds,
            )
        )

    async def close(self) -> None:
        await self._client.aclose()

    def get_gate_status(self) -> dict:
        return self._gate.get_status()

    async def ping(self) -> bool:
        try:
            await self._request_json(
                "/fapi/v1/ping",
                params={},
                weight=1,
            )
            return True
        except Exception as exc:
            log.warning("[binance] ping failed: %s: %s", type(exc).__name__, exc)
            return False

    async def get_exchange_info(self) -> dict | None:
        try:
            payload = await self._request_json(
                "/fapi/v1/exchangeInfo",
                params={},
                weight=1,
            )
            return payload
        except Exception as exc:
            log.warning("[binance] get_exchange_info failed: %s: %s", type(exc).__name__, exc)
            return None

    async def get_24hr_tickers(self) -> list[dict] | None:
        try:
            payload = await self._request_json(
                "/fapi/v1/ticker/24hr",
                params={},
                weight=40,
            )
            if isinstance(payload, list):
                return payload
            return None
        except Exception as exc:
            log.warning("[binance] get_24hr_tickers failed: %s: %s", type(exc).__name__, exc)
            return None

    async def get_mark_price(self, symbol: str) -> float | None:
        try:
            payload = await self._request_json(
                "/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                weight=1,
            )
            return float(payload["markPrice"])
        except Exception as exc:
            log.warning(
                "[binance] get_mark_price failed symbol=%s error=%s: %s",
                symbol,
                type(exc).__name__,
                exc,
            )
            return None

    async def get_last_closed_time(self, symbol: str, tf: str) -> int | None:
        bars = await self.fetch_klines(
            symbol=symbol,
            tf=tf,
            start_ms=None,
            end_ms=None,
            limit=2,
        )
        if not bars:
            return None
        if len(bars) == 1:
            return bars[0].close_time
        return bars[-2].close_time

    async def fetch_klines(
        self,
        *,
        symbol: str,
        tf: str,
        start_ms: int | None,
        end_ms: int | None,
        limit: int = 500,
    ) -> list[Bar]:
        params: dict[str, int | str] = {
            "symbol": symbol,
            "interval": tf,
            "limit": limit,
        }

        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)

        rows = await self._request_json(
            "/fapi/v1/klines",
            params=params,
            weight=1,
        )

        bars: list[Bar] = []
        for row in rows:
            open_time = int(row[0])
            open_price = float(row[1])
            high_price = float(row[2])
            low_price = float(row[3])
            close_price = float(row[4])
            volume = float(row[5])
            close_time = int(row[6])

            bars.append(
                Bar(
                    symbol=symbol,
                    tf=tf,
                    open_time=open_time,
                    close_time=close_time,
                    o=open_price,
                    h=high_price,
                    l=low_price,
                    c=close_price,
                    v=volume,
                    features=None,
                    features_ok=False,
                    features_ver="raw",
                )
            )

        return bars

    async def _request_json(
        self,
        path: str,
        *,
        params: dict,
        weight: int,
    ) -> dict | list:
        return await self._gate.execute_json(
            client=self._client,
            method="GET",
            path=path,
            params=params,
            weight=weight,
        )
