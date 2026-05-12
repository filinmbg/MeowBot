from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from meowbot.core.domain.enums import TradeStatus
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol


log = logging.getLogger("meowbot")


class StartupReconcileMissedExitsUseCase:
    """
    Після нічного/offline простою:
    - беремо всі OPEN trade
    - від exit_last_check_at доганяємо історію по 1m closed candles
    - послідовно проганяємо TP/SL логіку
    - зберігаємо вже відновлений стан trade
    """

    def __init__(
        self,
        *,
        trades_repo,
        exit_usecase,
        exchange,
        max_bars_per_request: int = 1000,
        reconcile_tf: str = "1m",
        live_sync_service=None,
        active_trades_cache=None,
    ) -> None:
        self.trades_repo = trades_repo
        self.exit_usecase = exit_usecase
        self.exchange = exchange
        self.max_bars_per_request = int(max_bars_per_request)
        self.reconcile_tf = reconcile_tf
        self.live_sync_service = live_sync_service
        self.active_trades_cache = active_trades_cache

    async def run(self, *, now_ms: int | None = None) -> dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        if self.active_trades_cache is not None:
            open_trades = self.active_trades_cache.get_all_open_trades()
        else:
            open_trades = await self.trades_repo.get_open_trades()
        if not open_trades:
            log.info("[startup-reconcile] no open trades")
            return {
                "open_trades": 0,
                "processed_trades": 0,
                "closed_during_reconcile": 0,
                "bars_processed": 0,
            }

        processed_trades = 0
        closed_during_reconcile = 0
        total_bars_processed = 0

        log.info("[startup-reconcile] start open_trades=%s", len(open_trades))

        for trade in open_trades:
            try:
                result = await self._reconcile_single_trade(
                    trade=trade,
                    now_ms=now_ms,
                )
                processed_trades += 1
                total_bars_processed += int(result["bars_processed"])
                if result["closed"]:
                    closed_during_reconcile += 1
            except Exception:
                log.exception(
                    "[startup-reconcile] failed trade_id=%s symbol=%s",
                    getattr(trade, "trade_id", "-"),
                    getattr(trade, "symbol", "-"),
                )

        summary = {
            "open_trades": len(open_trades),
            "processed_trades": processed_trades,
            "closed_during_reconcile": closed_during_reconcile,
            "bars_processed": total_bars_processed,
        }

        log.info("[startup-reconcile] done summary=%s", summary)
        return summary

    async def _reconcile_single_trade(self, *, trade, now_ms: int) -> dict[str, Any]:
        current = trade
        invalid_trade = await self._quarantine_invalid_symbol_if_needed(current, now_ms)
        if invalid_trade is not None:
            self._update_cache(invalid_trade)
            await self._persist_trade(invalid_trade)
            return {
                "bars_processed": 0,
                "closed": True,
            }

        if str(getattr(current, "mode", "")) == "live" and self.live_sync_service is not None:
            current = await self.live_sync_service.sync_trade(
                current,
                now_ms=now_ms,
                reason="startup_reconcile",
                force=True,
            )
            if self._is_closed(current):
                self._update_cache(current)
                await self._persist_trade(current)
                return {
                    "bars_processed": 0,
                    "closed": True,
                }

        symbol = str(current.symbol)
        from_ts = int(getattr(current, "exit_last_check_at", 0) or 0)

        if from_ts <= 0:
            log.warning(
                "[startup-reconcile] trade_id=%s symbol=%s has empty exit_last_check_at, using opened_at",
                current.trade_id,
                symbol,
            )
            from_ts = int(getattr(current, "opened_at", now_ms) or now_ms)

        # Не беремо поточну незакриту 1m свічку, тільки closed candles
        to_ts = self._floor_to_closed_minute(now_ms)

        if from_ts >= to_ts:
            return {
                "bars_processed": 0,
                "closed": False,
            }

        candles = await self._fetch_closed_1m_candles(
            symbol=symbol,
            start_ms=from_ts,
            end_ms=to_ts,
        )

        if not candles:
            return {
                "bars_processed": 0,
                "closed": False,
            }

        bars_processed = 0

        side = current.side.value if hasattr(current.side, "value") else str(current.side)

        for candle in candles:
            if self._is_closed(current):
                break

            candle_close_time = int(candle["close_time"])
            high_price = float(candle["high"])
            low_price = float(candle["low"])

            # Та сама логіка порядку, що і в online reconcile:
            # LONG: спочатку high для TP, потім low для SL
            # SHORT: спочатку low для TP, потім high для SL
            if side == "LONG":
                current = await self.exit_usecase._apply_price(current, high_price, candle_close_time)
                if not self._is_closed(current):
                    current = await self.exit_usecase._apply_price(current, low_price, candle_close_time)
            else:
                current = await self.exit_usecase._apply_price(current, low_price, candle_close_time)
                if not self._is_closed(current):
                    current = await self.exit_usecase._apply_price(current, high_price, candle_close_time)

            if not self._is_closed(current):
                current = replace(current, exit_last_check_at=candle_close_time)

            bars_processed += 1

        self._update_cache(current)
        await self._persist_trade(current)

        if self._is_closed(current):
            log.info(
                "[startup-reconcile] trade closed during replay trade_id=%s symbol=%s close_price=%s realized_pnl=%s bars=%s",
                current.trade_id,
                current.symbol,
                getattr(current, "close_price", None),
                getattr(current, "realized_pnl_usd", None),
                bars_processed,
            )
        else:
            log.info(
                "[startup-reconcile] trade replayed trade_id=%s symbol=%s bars=%s exit_last_check_at=%s",
                current.trade_id,
                current.symbol,
                bars_processed,
                getattr(current, "exit_last_check_at", None),
            )

        return {
            "bars_processed": bars_processed,
            "closed": self._is_closed(current),
        }

    async def _quarantine_invalid_symbol_if_needed(self, trade, now_ms: int):
        validation = validate_binance_usdt_perp_symbol(getattr(trade, "symbol", ""))
        if validation.valid:
            return None

        current = replace(
            trade,
            status=TradeStatus.ERROR_INVALID_SYMBOL,
            closed_at=now_ms,
            exit_last_check_at=now_ms,
            exit_reason="ERROR_INVALID_SYMBOL",
            exchange_sync_status="invalid_symbol",
            exchange_sync_error=validation.reason,
            qty_remaining=0.0,
            remaining_pct=0.0,
        )
        log.warning(
            "[startup-reconcile] INVALID_SYMBOL_SKIPPED trade_id=%s symbol=%s user=%s reason=%s action=quarantined",
            getattr(trade, "trade_id", "-"),
            validation.normalized_symbol or validation.symbol,
            getattr(trade, "user_id", "-"),
            validation.reason,
        )

        trade_events_repo = getattr(self.exit_usecase, "trade_events_repo", None)
        if trade_events_repo is not None:
            await trade_events_repo.add_event(
                trade_id=getattr(trade, "trade_id", f"invalid:{validation.normalized_symbol}"),
                event_type="INVALID_SYMBOL_SKIPPED",
                ts=now_ms,
                symbol=validation.normalized_symbol or validation.symbol,
                user_id=str(getattr(trade, "user_id", "system") or "system"),
                mode=str(getattr(trade, "mode", "sandbox") or "sandbox"),
                payload={
                    "reason": "invalid_symbol",
                    "invalid_reason": validation.reason,
                    "action": "startup_quarantined_without_binance_call",
                    "status": TradeStatus.ERROR_INVALID_SYMBOL.value,
                },
            )
        return current

    async def _fetch_closed_1m_candles(
        self,
        *,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """
        Нормалізований формат повернення:
        [
            {
                "open_time": ...,
                "close_time": ...,
                "open": ...,
                "high": ...,
                "low": ...,
                "close": ...,
            }
        ]
        """
        all_rows: list[dict[str, Any]] = []
        cursor = int(start_ms)

        while cursor < end_ms:
            rows = await self._fetch_klines_page(
                symbol=symbol,
                tf=self.reconcile_tf,
                start_ms=cursor,
                end_ms=end_ms,
                limit=self.max_bars_per_request,
            )
            if not rows:
                break

            normalized = [self._normalize_kline(x) for x in rows]
            normalized = [
                x for x in normalized
                if x["close_time"] > start_ms and x["close_time"] <= end_ms
            ]

            if not normalized:
                break

            all_rows.extend(normalized)

            last_close = normalized[-1]["close_time"]
            next_cursor = int(last_close) + 1

            if next_cursor <= cursor:
                break

            cursor = next_cursor

            if len(rows) < self.max_bars_per_request:
                break

        return all_rows

    async def _fetch_klines_page(
        self,
        *,
        symbol: str,
        tf: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[Any]:
        """
        Адаптер під різні назви методів exchange.
        Якщо у твоєму exchange є інша назва — достатньо поправити тут одне місце.
        """
        method_names = (
            "get_klines",
            "fetch_klines",
            "get_futures_klines",
        )

        for method_name in method_names:
            method = getattr(self.exchange, method_name, None)
            if callable(method):
                try:
                    return await method(
                        symbol=symbol,
                        tf=tf,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        limit=limit,
                    )
                except TypeError:
                    pass

                try:
                    return await method(
                        symbol=symbol,
                        interval=tf,
                        start_time=start_ms,
                        end_time=end_ms,
                        limit=limit,
                    )
                except TypeError:
                    pass

                try:
                    return await method(symbol, tf, start_ms, end_ms, limit)
                except TypeError:
                    pass

        raise RuntimeError(
            "No supported kline fetch method found on exchange. "
            "Add adapter in StartupReconcileMissedExitsUseCase._fetch_klines_page()."
        )

    def _normalize_kline(self, row):
        """
        Supports:
        - Binance raw kline (list)
        - Bar object
        """

        # ✅ Bar (твій домен)
        if hasattr(row, "open_time"):
            return {
                "open_time": int(row.open_time),
                "close_time": int(row.close_time),
                "open": float(row.o),
                "high": float(row.h),
                "low": float(row.l),
                "close": float(row.c),
            }

        # ✅ Binance list
        if isinstance(row, (list, tuple)):
            open_time = int(row[0])
            close_time = int(row[6]) if len(row) > 6 else open_time + 60000

            return {
                "open_time": open_time,
                "close_time": close_time,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }

        raise TypeError(f"Unsupported kline format: {type(row)}")

    @staticmethod
    def _floor_to_closed_minute(now_ms: int) -> int:
        minute_ms = 60_000
        return (now_ms // minute_ms) * minute_ms - 1

    @staticmethod
    def _is_closed(trade) -> bool:
        status = trade.status.value if hasattr(trade.status, "value") else str(trade.status)
        return status == "CLOSED"

    def _update_cache(self, trade) -> None:
        if self.active_trades_cache is None:
            return
        self.active_trades_cache.update_from_trade(trade)

    async def _persist_trade(self, trade) -> None:
        try:
            await self.trades_repo.update_trade(trade)
        except Exception as exc:
            log.warning(
                "[startup-reconcile] trade persistence skipped trade_id=%s symbol=%s error=%s:%s",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
                type(exc).__name__,
                exc,
            )
