from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta

import anyio

from meowbot.core.domain.enums import TradeStatus


log = logging.getLogger("meowbot")


class AsyncReconcileOpenTradesUseCase:
    def __init__(
        self,
        *,
        trades_repo,
        broker,
        trade_events_repo,
        price_provider,
        max_concurrency: int = 8,
        cooldowns_repo=None,
        telegram_users_repo=None,
    ) -> None:
        self.trades_repo = trades_repo
        self.broker = broker
        self.trade_events_repo = trade_events_repo
        self.price_provider = price_provider
        self.cooldowns_repo = cooldowns_repo
        self.telegram_users_repo = telegram_users_repo
        self._sem = anyio.Semaphore(max_concurrency)

        # TP1 = 0.5%, TP2 = 1.0%, TP3 = 1.5%
        self.tp_levels = [0.005, 0.01, 0.015]

    async def run(self, now_ms: int) -> None:
        open_trades = await self.trades_repo.get_open_trades()

        if open_trades:
            log.info("[exit-async] open_trades=%d end=%s", len(open_trades), now_ms)

        async with anyio.create_task_group() as tg:
            for trade in open_trades:
                tg.start_soon(self._process_trade_safe, trade, now_ms)

    async def _process_trade_safe(self, trade, now_ms: int) -> None:
        try:
            await self._process_trade(trade, now_ms)
        except Exception:
            log.exception(
                "[exit-async] trade_id=%s symbol=%s unexpected error",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
            )

    async def _process_trade(self, trade, now_ms: int) -> None:
        async with self._sem:
            symbol = trade.symbol
            price = self.price_provider.get_price(symbol)

            if price is None:
                log.debug(
                    "[exit-async] no ws price yet trade_id=%s symbol=%s",
                    trade.trade_id,
                    symbol,
                )
                return

            current = trade
            current = await self._apply_price(current, price, now_ms)

            if self._status(current) != TradeStatus.CLOSED.value:
                current = replace(current, exit_last_check_at=now_ms)

            await self.trades_repo.update_trade(current)

    async def _apply_price(self, trade, price: float, now_ms: int):
        current = trade
        side = trade.side.value if hasattr(trade.side, "value") else str(trade.side)

        if side == "LONG":
            current, tp_triggered = await self._apply_long_tp_logic(current, price, now_ms)
            if self._status(current) == TradeStatus.CLOSED.value:
                return current

            if tp_triggered:
                return current

            if current.qty_remaining > 0 and price <= current.sl_price:
                current = self.broker.close_position(
                    current,
                    price=current.sl_price,
                    reason="STOP_LOSS_HIT",
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="CLOSED",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "reason": "STOP_LOSS_HIT",
                        "close_price": current.close_price,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "tp_hit_count": current.tp_hit_count,
                    },
                )
                await self._apply_loss_cooldown_if_needed(current, now_ms)
                log.info(
                    "[exit-async] %s trade_id=%s CLOSED reason=%s close_price=%s realized_pnl=%s",
                    current.symbol,
                    current.trade_id,
                    "STOP_LOSS_HIT",
                    current.close_price,
                    current.realized_pnl_usd,
                )
                return current

            return current

        current, tp_triggered = await self._apply_short_tp_logic(current, price, now_ms)
        if self._status(current) == TradeStatus.CLOSED.value:
            return current

        if tp_triggered:
            return current

        if current.qty_remaining > 0 and price >= current.sl_price:
            current = self.broker.close_position(
                current,
                price=current.sl_price,
                reason="STOP_LOSS_HIT",
            )
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "STOP_LOSS_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            await self._apply_loss_cooldown_if_needed(current, now_ms)
            log.info(
                "[exit-async] %s trade_id=%s CLOSED reason=%s close_price=%s realized_pnl=%s",
                current.symbol,
                current.trade_id,
                "STOP_LOSS_HIT",
                current.close_price,
                current.realized_pnl_usd,
            )
        return current

    async def _apply_loss_cooldown_if_needed(self, trade, now_ms: int) -> None:
        if self.cooldowns_repo is None:
            return

        realized_pnl = float(getattr(trade, "realized_pnl_usd", 0.0) or 0.0)
        if realized_pnl >= 0:
            return

        cooldown_enabled = True
        cooldown_minutes = 120
        user_uuid = None

        if self.telegram_users_repo is not None:
            tg_user = await self.telegram_users_repo.get_by_trading_user_id(str(trade.user_id))
            if tg_user:
                cooldown_enabled = bool(tg_user.get("loss_cooldown_enabled", True))
                cooldown_minutes = int(tg_user.get("loss_cooldown_minutes", 120) or 120)

        if not cooldown_enabled or cooldown_minutes <= 0:
            return

        cooldown_until = self.cooldowns_repo.utc_now() + timedelta(minutes=cooldown_minutes)

        await self.cooldowns_repo.upsert_loss_stop_cooldown(
            runtime_user_id=str(trade.user_id),
            user_id=user_uuid,
            symbol=str(trade.symbol),
            cooldown_until=cooldown_until,
        )

        await self.trade_events_repo.add_event(
            trade_id=trade.trade_id,
            event_type="LOSS_STOP_COOLDOWN_SET",
            ts=now_ms,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload={
                "cooldown_minutes": cooldown_minutes,
                "cooldown_until": cooldown_until.isoformat(),
                "realized_pnl_usd": realized_pnl,
            },
        )

        log.info(
            "[exit-async] cooldown set user=%s symbol=%s until=%s pnl=%s",
            trade.user_id,
            trade.symbol,
            cooldown_until,
            realized_pnl,
        )

    async def _apply_long_tp_logic(self, trade, price: float, now_ms: int):
        current = trade
        tp_triggered = False

        while current.tp_hit_count < 3 and current.qty_remaining > 0:
            next_tp_index = current.tp_hit_count
            tp_price = current.entry_price * (1.0 + self.tp_levels[next_tp_index])

            if price < tp_price:
                break

            tp_triggered = True

            if next_tp_index == 0:
                qty_to_reduce = current.qty_remaining * 0.70
                current = self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP1_HIT",
                )
                current = replace(
                    current,
                    tp_hit_count=1,
                    sl_price=current.entry_price,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 1,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-async] %s trade_id=%s TP1 hit tp_price=%s qty_remaining=%s realized_pnl=%s",
                    current.symbol,
                    current.trade_id,
                    tp_price,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            if next_tp_index == 1:
                qty_to_reduce = min(current.qty_remaining, current.qty * 0.20)
                current = self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP2_HIT",
                )
                current = replace(
                    current,
                    tp_hit_count=2,
                    sl_price=current.entry_price * 1.002,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 2,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-async] %s trade_id=%s TP2 hit tp_price=%s qty_remaining=%s realized_pnl=%s",
                    current.symbol,
                    current.trade_id,
                    tp_price,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            current = self.broker.close_position(
                current,
                price=tp_price,
                reason="TP3_HIT",
            )
            current = replace(current, tp_hit_count=3)
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "TP3_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            log.info(
                "[exit-async] %s trade_id=%s CLOSED reason=%s close_price=%s realized_pnl=%s",
                current.symbol,
                current.trade_id,
                "TP3_HIT",
                current.close_price,
                current.realized_pnl_usd,
            )
            break

        return current, tp_triggered

    async def _apply_short_tp_logic(self, trade, price: float, now_ms: int):
        current = trade
        tp_triggered = False

        while current.tp_hit_count < 3 and current.qty_remaining > 0:
            next_tp_index = current.tp_hit_count
            tp_price = current.entry_price * (1.0 - self.tp_levels[next_tp_index])

            if price > tp_price:
                break

            tp_triggered = True

            if next_tp_index == 0:
                qty_to_reduce = current.qty_remaining * 0.70
                current = self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP1_HIT",
                )
                current = replace(
                    current,
                    tp_hit_count=1,
                    sl_price=current.entry_price,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 1,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-async] %s trade_id=%s TP1 hit tp_price=%s qty_remaining=%s realized_pnl=%s",
                    current.symbol,
                    current.trade_id,
                    tp_price,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            if next_tp_index == 1:
                qty_to_reduce = min(current.qty_remaining, current.qty * 0.20)
                current = self.broker.reduce_position(
                    current,
                    qty_to_reduce=qty_to_reduce,
                    price=tp_price,
                    reason="TP2_HIT",
                )
                current = replace(
                    current,
                    tp_hit_count=2,
                    sl_price=current.entry_price * 0.998,
                )
                await self.trade_events_repo.add_event(
                    trade_id=current.trade_id,
                    event_type="TP_HIT",
                    ts=now_ms,
                    symbol=current.symbol,
                    user_id=current.user_id,
                    mode=current.mode,
                    payload={
                        "tp_index": 2,
                        "tp_price": tp_price,
                        "qty_remaining": current.qty_remaining,
                        "realized_pnl_usd": current.realized_pnl_usd,
                        "new_sl_price": current.sl_price,
                    },
                )
                log.info(
                    "[exit-async] %s trade_id=%s TP2 hit tp_price=%s qty_remaining=%s realized_pnl=%s",
                    current.symbol,
                    current.trade_id,
                    tp_price,
                    current.qty_remaining,
                    current.realized_pnl_usd,
                )
                continue

            current = self.broker.close_position(
                current,
                price=tp_price,
                reason="TP3_HIT",
            )
            current = replace(current, tp_hit_count=3)
            await self.trade_events_repo.add_event(
                trade_id=current.trade_id,
                event_type="CLOSED",
                ts=now_ms,
                symbol=current.symbol,
                user_id=current.user_id,
                mode=current.mode,
                payload={
                    "reason": "TP3_HIT",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                },
            )
            log.info(
                "[exit-async] %s trade_id=%s CLOSED reason=%s close_price=%s realized_pnl=%s",
                current.symbol,
                current.trade_id,
                "TP3_HIT",
                current.close_price,
                current.realized_pnl_usd,
            )
            break

        return current, tp_triggered

    def _status(self, trade) -> str:
        return trade.status.value if hasattr(trade.status, "value") else str(trade.status)