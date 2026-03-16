import asyncio
import time
import logging

from meowbot.infra.mongo.client import MongoConn, MongoConfig
from meowbot.infra.mongo.migrations import apply_migrations
from meowbot.infra.mongo.repos.bars_repo import BarsRepositoryMongo
from meowbot.infra.mongo.repos.trades_repo import TradesRepositoryMongo
from meowbot.infra.mongo.repos.bot_state_repo import BotStateRepositoryMongo
from meowbot.infra.mongo.repos.trade_events_repo import TradeEventsRepositoryMongo

from meowbot.infra.exchange.binance_futures_usdtm import BinanceFuturesMarketData, BinanceFuturesConfig
from meowbot.infra.broker.paper import PaperBroker

from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.core.usecases.ensure_market_data import EnsureMarketDataUseCase
from meowbot.core.usecases.online_entry_cycle import OnlineEntryCycleUseCase
from meowbot.core.usecases.reconcile_trades import ReconcileOpenTradesUseCase

from meowbot.infra.memory.policy import FakeThresholdPolicy
from meowbot.core.services.execution.entry.policy_entry_strategy import PolicyEntryStrategy

from meowbot.apps.bot_online.runner_async import main_async


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("meowbot")


SYMBOLS = ["BTCUSDT"]
TFS = ["15m"]
FEATURES_VER = "v1"


async def entry_job():
    now_ms = int(time.time() * 1000)
    for sym in SYMBOLS:
        for tf in TFS:
            uc_entry.run(sym, tf, now_ms)


async def exit_job():
    now_ms = int(time.time() * 1000)
    uc_exit.run(now_ms)


if __name__ == "__main__":
    conn = MongoConn(MongoConfig())
    conn.connect()

    apply_migrations(conn.db, timeframes=("1m", "15m", "1h", "4h", "1d"))

    bars_repo = BarsRepositoryMongo(conn.db)
    trades_repo = TradesRepositoryMongo(conn.db)
    bot_state_repo = BotStateRepositoryMongo(conn.db)
    trade_events_repo = TradeEventsRepositoryMongo(conn.db)

    exchange = BinanceFuturesMarketData(BinanceFuturesConfig())
    if not exchange.ping():
        raise RuntimeError("Binance Futures API ping failed")

    broker = PaperBroker()
    gate = PortfolioGate(trades_repo)

    # startup warmup
    warmup = EnsureMarketDataUseCase(
        bars_repo=bars_repo,
        exchange=exchange,
        features_ver=FEATURES_VER,
        required_tail=150,
    )
    for sym in SYMBOLS:
        for tf in TFS:
            warmup.run(sym, tf)

    # strategy / policy
    policy = FakeThresholdPolicy(threshold=0)
    strategy = PolicyEntryStrategy(policy)

    global uc_entry
    global uc_exit

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

    asyncio.run(main_async(entry_job, exit_job))