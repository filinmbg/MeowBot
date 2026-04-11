from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Awaitable

import websockets
from websockets.asyncio.client import ClientConnection


log = logging.getLogger("meowbot")


PriceCallback = Callable[[str, float, int], Awaitable[None] | None]


class BinanceFuturesWsPrices:
    """
    Live WS price provider with hot subscribe/unsubscribe support.

    Працює через Binance USDⓈ-M Futures market websocket.
    Підтримує:
    - локальний кеш останньої ціни
    - reconnect
    - гарячу зміну підписок без reconnect
    - mark price або ticker streams
    """

    def __init__(
        self,
        *,
        symbols_provider: Callable[[], list[str]],
        ws_base_url: str = "wss://fstream.binance.com/market/ws",
        reconnect_delay_seconds: float = 5.0,
        subscription_sync_interval_seconds: float = 3.0,
        use_mark_price_stream: bool = True,
        price_callback: PriceCallback | None = None,
    ) -> None:
        self.symbols_provider = symbols_provider
        self.ws_base_url = ws_base_url.rstrip("/")
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.subscription_sync_interval_seconds = subscription_sync_interval_seconds
        self.use_mark_price_stream = use_mark_price_stream
        self.price_callback = price_callback

        self._latest_prices: dict[str, float] = {}
        self._latest_event_time_ms: dict[str, int] = {}

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self._request_id = 0
        self._subscribed_streams: set[str] = set()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_forever())
        log.info("[ws-prices] started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except Exception:
                log.exception("[ws-prices] stop failed")
        log.info("[ws-prices] stopped")

    def get_price(self, symbol: str) -> float | None:
        return self._latest_prices.get(symbol.upper())

    def get_event_time_ms(self, symbol: str) -> int | None:
        return self._latest_event_time_ms.get(symbol.upper())

    async def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                log.info("[ws-prices] connecting url=%s", self.ws_base_url)

                async with websockets.connect(
                    self.ws_base_url,
                    ping_interval=150,   # Binance server sends ping itself; keep client quiet
                    ping_timeout=30,
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    self._subscribed_streams.clear()
                    await self._sync_subscriptions(ws, force_full_sync=True)

                    log.info(
                        "[ws-prices] connected symbols=%s streams=%s",
                        len(self.symbols_provider()),
                        len(self._subscribed_streams),
                    )

                    next_sync_at = asyncio.get_running_loop().time() + self.subscription_sync_interval_seconds

                    while not self._stop_event.is_set():
                        timeout = max(0.1, next_sync_at - asyncio.get_running_loop().time())

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                            await self._handle_message(raw)
                        except asyncio.TimeoutError:
                            await self._sync_subscriptions(ws, force_full_sync=False)
                            next_sync_at = asyncio.get_running_loop().time() + self.subscription_sync_interval_seconds

            except Exception as exc:
                log.warning("[ws-prices] connection error=%s: %s", type(exc).__name__, exc)

            if not self._stop_event.is_set():
                await asyncio.sleep(self.reconnect_delay_seconds)

    async def _sync_subscriptions(self, ws: ClientConnection, *, force_full_sync: bool) -> None:
        desired = self._build_desired_streams()

        if force_full_sync:
            to_subscribe = sorted(desired)
            to_unsubscribe: list[str] = []
        else:
            to_subscribe = sorted(desired - self._subscribed_streams)
            to_unsubscribe = sorted(self._subscribed_streams - desired)

        if to_unsubscribe:
            await self._send_control(
                ws,
                method="UNSUBSCRIBE",
                params=to_unsubscribe,
            )
            for stream in to_unsubscribe:
                self._subscribed_streams.discard(stream)

            log.info("[ws-prices] unsubscribed streams=%s", len(to_unsubscribe))

        if to_subscribe:
            await self._send_control(
                ws,
                method="SUBSCRIBE",
                params=to_subscribe,
            )
            for stream in to_subscribe:
                self._subscribed_streams.add(stream)

            log.info("[ws-prices] subscribed streams=%s total=%s", len(to_subscribe), len(self._subscribed_streams))

    async def _send_control(self, ws: ClientConnection, *, method: str, params: list[str]) -> None:
        if not params:
            return

        self._request_id += 1
        payload = {
            "method": method,
            "params": params,
            "id": self._request_id,
        }
        await ws.send(json.dumps(payload))

    def _build_desired_streams(self) -> set[str]:
        symbols = [s.strip().lower() for s in self.symbols_provider() if s and s.strip()]
        if self.use_mark_price_stream:
            return {f"{symbol}@markPrice@1s" for symbol in symbols}
        return {f"{symbol}@ticker" for symbol in symbols}

    async def _handle_message(self, raw: str) -> None:
        payload = json.loads(raw)

        # ACK / control responses
        if "result" in payload and "id" in payload:
            return

        data = payload.get("data") or payload
        event_type = data.get("e")

        if event_type == "markPriceUpdate":
            symbol = str(data.get("s", "")).upper()
            price_raw = data.get("p")
            event_time_ms = int(data.get("E", 0) or 0)
        elif event_type == "24hrTicker":
            symbol = str(data.get("s", "")).upper()
            price_raw = data.get("c")
            event_time_ms = int(data.get("E", 0) or 0)
        else:
            return

        if not symbol or price_raw is None:
            return

        try:
            price = float(price_raw)
        except Exception:
            return

        self._latest_prices[symbol] = price
        self._latest_event_time_ms[symbol] = event_time_ms

        if self.price_callback is not None:
            result = self.price_callback(symbol, price, event_time_ms)
            if asyncio.iscoroutine(result):
                await result