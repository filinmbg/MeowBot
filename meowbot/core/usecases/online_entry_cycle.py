from __future__ import annotations

import logging
from typing import List

from meowbot.core.domain.types import Bar, Trade
from meowbot.core.domain.enums import Side, TradeStatus, SignalAction
from meowbot.core.ports.bars_repo import BarsRepository
from meowbot.core.ports.bot_state_repo import BotStateRepository
from meowbot.core.ports.trades_repo import TradesRepository
from meowbot.core.ports.exchange_market_data import ExchangeMarketData
from meowbot.core.ports.entry_strategy import EntryStrategy
from meowbot.core.ports.broker import Broker
from meowbot.core.ports.trade_events_repo import TradeEventsRepository
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate

log = logging.getLogger("meowbot")


class OnlineEntryCycleUseCase:
    def __init__(
        self,
        bars_repo: BarsRepository,
        trades_repo: TradesRepository,
        bot_state_repo: BotStateRepository,
        exchange: ExchangeMarketData,
        strategy: EntryStrategy,
        gate: PortfolioGate,
        broker: Broker,
        trade_events_repo: TradeEventsRepository,
        features_ver: str,
    ):
        self.bars_repo = bars_repo
        self.trades_repo = trades_repo
        self.bot_state_repo = bot_state_repo
        self.exchange = exchange
        self.strategy = strategy
        self.gate = gate
        self.broker = broker
        self.trade_events_repo = trade_events_repo
        self.features_ver = features_ver

    def _cursor_key(self, symbol: str, tf: str) -> str:
        return f"entry_cursor:{symbol}:{tf}:{self.features_ver}"

    def run(self, symbol: str, tf: str, now_ms: int) -> None:
        last_ex = self.exchange.get_last_closed_time(symbol, tf)
        if last_ex is None:
            log.info("[entry] %s %s: no last_ex", symbol, tf)
            return

        cursor_key = self._cursor_key(symbol, tf)
        last_decision = self.bot_state_repo.get_int(cursor_key, default=0)

        if last_decision == 0:
            self.bot_state_repo.set_int(cursor_key, last_ex)
            log.info(
                "[entry] %s %s: cursor initialized to current last_ex=%s (live mode, no replay)",
                symbol,
                tf,
                last_ex,
            )
            return

        if last_ex <= last_decision:
            log.info(
                "[entry] %s %s: no new closed bar (last_ex=%s, cursor=%s)",
                symbol,
                tf,
                last_ex,
                last_decision,
            )
            return

        log.info(
            "[entry] %s %s: NEW closed bar(s) detected (last_ex=%s > cursor=%s)",
            symbol,
            tf,
            last_ex,
            last_decision,
        )

        last_db = self.bars_repo.get_last_close_time(symbol, tf, self.features_ver)
        start = last_db or 0

        raw = self.exchange.fetch_klines(symbol, tf, start, last_ex)
        log.info(
            "[entry] %s %s: fetched %d klines (start=%s, end=%s)",
            symbol,
            tf,
            len(raw),
            start,
            last_ex,
        )

        ready: List[Bar] = []
        for b in raw:
            d = dict(b.__dict__)
            d["features_ok"] = True
            d["features_ver"] = self.features_ver
            ready.append(Bar(**d))

        self.bars_repo.upsert_many(ready)
        log.info("[entry] %s %s: upserted %d bars to Mongo", symbol, tf, len(ready))

        new_bars = [b for b in ready if b.close_time > last_decision]
        new_bars.sort(key=lambda x: x.close_time)

        log.info("[entry] %s %s: %d new bars to evaluate", symbol, tf, len(new_bars))

        for bar in new_bars:
            signal = self.strategy.decide(bar)
            log.info(
                "[entry] %s %s: bar_close=%s signal=%s",
                symbol,
                tf,
                bar.close_time,
                getattr(signal, "action", signal),
            )

            if not self.gate.allow(symbol, signal):
                log.info("[entry] %s %s: gate blocked at bar_close=%s", symbol, tf, bar.close_time)
                self.bot_state_repo.set_int(cursor_key, bar.close_time)
                continue

            user_id = "demo_user"
            stake_usd = 1.0
            leverage = 20
            entry_price = float(bar.c)
            qty = (stake_usd * leverage) / entry_price

            if signal.action == SignalAction.LONG:
                side = Side.LONG
                sl_price = entry_price * 0.99
            elif signal.action == SignalAction.SHORT:
                side = Side.SHORT
                sl_price = entry_price * 1.01
            else:
                log.info("[entry] %s %s: HOLD - skip", symbol, tf)
                self.bot_state_repo.set_int(cursor_key, bar.close_time)
                continue

            trade_id = f"{user_id}:{symbol}:{tf}:{bar.close_time}"

            t = Trade(
                trade_id=trade_id,
                user_id=user_id,
                symbol=symbol,
                side=side,
                status=TradeStatus.OPEN,
                opened_at=now_ms,
                entry_price=entry_price,
                qty=qty,
                leverage=leverage,
                stake_usd=stake_usd,
                tf_entry=tf,
                model_id="policy_demo",
                entry_bar_close_time=bar.close_time,
                sl_price=sl_price,
                mode="sandbox",
                tp_hit_count=0,
                remaining_pct=1.0,
                exit_last_check_at=now_ms,
                qty_remaining=qty,
                realized_pnl_usd=0.0,
            )

            try:
                t = self.broker.open_position(t)
                self.trades_repo.create_trade(t)

                self.trade_events_repo.add_event(
                    trade_id=t.trade_id,
                    event_type="OPENED",
                    ts=now_ms,
                    symbol=t.symbol,
                    user_id=t.user_id,
                    mode=t.mode,
                    payload={
                        "entry_price": t.entry_price,
                        "qty": t.qty,
                        "sl_price": t.sl_price,
                        "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                        "tf_entry": t.tf_entry,
                    },
                )

                log.info(
                    "[entry] %s %s: CREATED trade_id=%s entry=%s qty=%s sl=%s mode=%s",
                    symbol,
                    tf,
                    trade_id,
                    entry_price,
                    qty,
                    sl_price,
                    t.mode,
                )
            except Exception as e:
                log.info("[entry] %s %s: trade not created (%s)", symbol, tf, type(e).__name__)
            finally:
                self.bot_state_repo.set_int(cursor_key, bar.close_time)
                log.info("[entry] %s %s: cursor updated to %s", symbol, tf, bar.close_time)