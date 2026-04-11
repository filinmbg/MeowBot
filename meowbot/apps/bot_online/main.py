from __future__ import annotations

import asyncio
import logging
import time

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.bars_repo import BarsRepositoryMongo
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo
from meowbot.infra.mongo.repos.bot_state_repo import BotStateRepositoryMongo
from meowbot.infra.mongo.repos.trade_events_repo import TradeEventsRepositoryMongo

from meowbot.infra.exchange.binance_futures_usdtm import (
    BinanceFuturesConfig,
    BinanceFuturesMarketData,
)
from meowbot.infra.broker.paper import PaperBroker

from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.core.usecases.ensure_market_data import EnsureMarketDataUseCase
from meowbot.core.usecases.online_entry_cycle import OnlineEntryCycleUseCase
from meowbot.core.usecases.reconcile_trades import ReconcileOpenTradesUseCase

# Тимчасово лишаємо для сумісності конструктора OnlineEntryCycleUseCase.
# Усередині online_entry_cycle strategy вже не використовується як основний мозок,
# бо рішення приймає detector.
from meowbot.infra.memory.policy import FakeThresholdPolicy
from meowbot.core.services.execution.entry.policy_entry_strategy import PolicyEntryStrategy


logging.basicConfig(level=logging.INFO)
logging.getLogger("meowbot").setLevel(logging.INFO)
log = logging.getLogger("meowbot")


# Стартуємо з 5 монет для стабільного нічного запуску.
# Після перевірки можна розширити до 10.
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "TRXUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "ETCUSDT",
    "ATOMUSDT",
    "XLMUSDT",
    "UNIUSDT",
    "NEARUSDT",
    "APTUSDT",
    "FILUSDT",
]

TFS = ["15m", "30m", "1h", "2h", "4h"]
FEATURES_VER = "v2_core"

ENTRY_POLL_SEC = 15
EXIT_POLL_SEC = 5
WARMUP_REQUIRED_TAIL = 500


async def main() -> None:
    conn = MongoConn(MongoConfig())
    conn.connect()

    apply_migrations(
        conn.db,
        timeframes=("1m", "15m", "30m", "1h", "2h", "4h", "1d"),
    )

    bars_repo = BarsRepositoryMongo(conn.db)
    trades_repo = TradesRepositoryMongo(conn.db)
    bot_state_repo = BotStateRepositoryMongo(conn.db)
    trade_events_repo = TradeEventsRepositoryMongo(conn.db)

    exchange = BinanceFuturesMarketData(BinanceFuturesConfig())
    if not exchange.ping():
        raise RuntimeError("Binance Futures API ping failed")

    broker = PaperBroker()
    gate = PortfolioGate(trades_repo)

    warmup = EnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver=FEATURES_VER,
        required_tail=WARMUP_REQUIRED_TAIL,
    )

    log.info(
        "[startup] config symbols=%s tfs=%s features_ver=%s warmup_tail=%s",
        SYMBOLS,
        TFS,
        FEATURES_VER,
        WARMUP_REQUIRED_TAIL,
    )

    log.info("[startup] warmup start")
    for sym in SYMBOLS:
        for tf in TFS:
            try:
                log.info("[startup] warmup %s %s start", sym, tf)
                warmup.run(sym, tf)

                ready_bars = bars_repo.get_tail(
                    symbol=sym,
                    tf=tf,
                    n=5,
                    features_ver=FEATURES_VER,
                    require_features_ok=True,
                )

                last_ready_close = ready_bars[-1].close_time if ready_bars else None

                log.info(
                    "[startup] warmup %s %s done ready_bars=%s last_ready_close=%s",
                    sym,
                    tf,
                    len(ready_bars),
                    last_ready_close,
                )
            except Exception:
                log.exception("[startup] warmup failed for %s %s", sym, tf)

    log.info("[startup] warmup done")

    policy = FakeThresholdPolicy(threshold=0)
    strategy = PolicyEntryStrategy(policy)

    uc_entry = OnlineEntryCycleUseCase(
        bars_repo=bars_repo,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        exchange=exchange,
        strategy=strategy,
        gate=gate,
        broker=broker,
        trade_events_repo=trade_events_repo,
        features_ver=FEATURES_VER,
    )

    uc_exit = ReconcileOpenTradesUseCase(
        trades_repo=trades_repo,
        exchange=exchange,
        broker=broker,
        trade_events_repo=trade_events_repo,
    )

    async def entry_loop() -> None:
        while True:
            try:
                now_ms = int(time.time() * 1000)

                # 1. Оновлюємо market data + online indicators
                for sym in SYMBOLS:
                    for tf in TFS:
                        try:
                            warmup.run(sym, tf)
                        except Exception:
                            log.exception("[entry_loop] warmup failed for %s %s", sym, tf)

                # 2. Перевіряємо entry сигнали по вже готових feature-ready барах
                for sym in SYMBOLS:
                    for tf in TFS:
                        try:
                            uc_entry.run(sym, tf, now_ms)
                        except Exception:
                            log.exception("[entry_loop] entry failed for %s %s", sym, tf)

            except Exception:
                log.exception("[entry_loop] failed")

            await asyncio.sleep(ENTRY_POLL_SEC)

    async def exit_loop() -> None:
        while True:
            try:
                now_ms = int(time.time() * 1000)
                uc_exit.run(now_ms)
            except Exception:
                log.exception("[exit_loop] failed")

            await asyncio.sleep(EXIT_POLL_SEC)

    log.info("[startup] bot_online worker started")
    await asyncio.gather(
        entry_loop(),
        exit_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())