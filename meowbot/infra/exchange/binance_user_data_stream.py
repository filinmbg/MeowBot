from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import websockets

from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache


log = logging.getLogger("meowbot")


@dataclass(frozen=True)
class BinanceUserDataStreamConfig:
    ws_base_url: str = "wss://fstream.binance.com/ws"
    refresh_users_interval_seconds: float = 15.0
    reconnect_delay_seconds: float = 5.0
    keepalive_interval_seconds: float = 25 * 60.0
    message_timeout_seconds: float = 15.0


class BinanceUserDataStreamSupervisor:
    def __init__(
        self,
        *,
        runtime_repo,
        trades_repo,
        account_provider,
        live_sync_service,
        active_trades_cache=None,
        config: BinanceUserDataStreamConfig | None = None,
    ) -> None:
        self.runtime_repo = runtime_repo
        self.trades_repo = trades_repo
        self.account_provider = account_provider
        self.live_sync_service = live_sync_service
        self.active_trades_cache = active_trades_cache or ActiveTradesCache()
        self.config = config or BinanceUserDataStreamConfig()

        self._stop_event = asyncio.Event()
        self._supervisor_task: asyncio.Task | None = None
        self._workers: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}

    async def start(self) -> None:
        if self._supervisor_task and not self._supervisor_task.done():
            return
        self._stop_event.clear()
        self._supervisor_task = asyncio.create_task(self._run())
        log.info("[user-stream] supervisor started")

    async def stop(self) -> None:
        self._stop_event.set()
        for runtime_user_id, (_, worker_stop) in list(self._workers.items()):
            worker_stop.set()
        if self._supervisor_task:
            await self._supervisor_task
        log.info("[user-stream] supervisor stopped")

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                desired_users = await self._load_desired_users()
                current_users = set(self._workers)

                for runtime_user_id in sorted(desired_users - current_users):
                    worker_stop = asyncio.Event()
                    task = asyncio.create_task(self._run_user_worker(runtime_user_id, worker_stop))
                    self._workers[runtime_user_id] = (task, worker_stop)
                    log.info("[user-stream] worker started user=%s", runtime_user_id)

                for runtime_user_id in sorted(current_users - desired_users):
                    task, worker_stop = self._workers.pop(runtime_user_id)
                    worker_stop.set()
                    await task
                    log.info("[user-stream] worker stopped user=%s", runtime_user_id)

                await asyncio.sleep(self.config.refresh_users_interval_seconds)
        finally:
            for runtime_user_id, (task, worker_stop) in list(self._workers.items()):
                worker_stop.set()
                await task
                log.info("[user-stream] worker stopped user=%s", runtime_user_id)
            self._workers.clear()

    async def _load_desired_users(self) -> set[str]:
        desired: set[str] = set(self.active_trades_cache.list_open_live_user_ids())
        runtime_rows = await self.runtime_repo.list_enabled_trading_users()
        for row in runtime_rows:
            runtime_user_id = str(row.get("trading_user_id") or "")
            if not runtime_user_id:
                continue
            if str(row.get("trading_mode") or "") == "live":
                desired.add(runtime_user_id)
        return desired

    async def _run_user_worker(self, runtime_user_id: str, worker_stop: asyncio.Event) -> None:
        while not self._stop_event.is_set() and not worker_stop.is_set():
            client = None
            listen_key = None
            keepalive_task = None
            try:
                client, _, error = await self.account_provider.build_client(
                    runtime_user_id=runtime_user_id,
                    require_active=False,
                )
                if client is None:
                    log.warning("[user-stream] no client user=%s reason=%s", runtime_user_id, error)
                    await asyncio.sleep(self.config.reconnect_delay_seconds)
                    continue

                listen_key = await client.start_user_data_stream()
                keepalive_task = asyncio.create_task(
                    self._keepalive_loop(client, listen_key, worker_stop)
                )

                ws_url = f"{self.config.ws_base_url.rstrip('/')}/{listen_key}"
                log.info("[user-stream] connecting user=%s", runtime_user_id)

                async with websockets.connect(
                    ws_url,
                    ping_interval=150,
                    ping_timeout=30,
                    close_timeout=10,
                    max_size=2**20,
                ) as ws:
                    while not self._stop_event.is_set() and not worker_stop.is_set():
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(),
                                timeout=self.config.message_timeout_seconds,
                            )
                        except asyncio.TimeoutError:
                            continue

                        payload = json.loads(raw)
                        event_type = str(payload.get("e") or "")
                        if event_type not in {"ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "listenKeyExpired"}:
                            continue
                        if event_type == "listenKeyExpired":
                            log.warning("[user-stream] listen key expired user=%s", runtime_user_id)
                            break

                        await self.live_sync_service.handle_user_stream_event(
                            runtime_user_id=runtime_user_id,
                            payload=payload,
                            now_ms=int(payload.get("E") or time.time() * 1000),
                        )
            except Exception as exc:
                log.warning("[user-stream] worker error user=%s error=%s: %s", runtime_user_id, type(exc).__name__, exc)
            finally:
                if keepalive_task is not None:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        log.exception("[user-stream] keepalive stop failed user=%s", runtime_user_id)

                if client is not None and listen_key is not None:
                    try:
                        await client.close_user_data_stream(listen_key=listen_key)
                    except Exception:
                        log.debug("[user-stream] close listen key failed user=%s", runtime_user_id)

                if client is not None:
                    await client.close()

            if not self._stop_event.is_set() and not worker_stop.is_set():
                await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _keepalive_loop(self, client, listen_key: str, worker_stop: asyncio.Event) -> None:
        while not self._stop_event.is_set() and not worker_stop.is_set():
            await asyncio.sleep(self.config.keepalive_interval_seconds)
            await client.keepalive_user_data_stream(listen_key=listen_key)
