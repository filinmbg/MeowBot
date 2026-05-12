from __future__ import annotations

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.usecases.get_paper_trade_stats import GetPaperTradeStatsUseCase


class FakeTradesRepo:
    def __init__(self, trades: list[Trade]) -> None:
        self.trades = trades

    def get_trades(
        self,
        *,
        user_id: str | None = None,
        symbol: str | None = None,
        tf: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        limit: int = 1000,
    ) -> list[Trade]:
        result = self.trades

        if user_id is not None:
            result = [t for t in result if t.user_id == user_id]
        if symbol is not None:
            result = [t for t in result if t.symbol == symbol]
        if tf is not None:
            result = [t for t in result if t.tf_entry == tf]
        if mode is not None:
            result = [t for t in result if t.mode == mode]
        if model_id is not None:
            result = [t for t in result if t.model_id == model_id]

        return result[:limit]


def _make_trade(
    *,
    trade_id: str,
    user_id: str,
    symbol: str,
    tf: str,
    model_id: str,
    status: TradeStatus,
    pnl: float,
    mode: str = "sandbox",
) -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id=user_id,
        symbol=symbol,
        side=Side.LONG,
        status=status,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=20,
        stake_usd=10.0,
        tf_entry=tf,
        model_id=model_id,
        entry_bar_close_time=1,
        sl_price=98.0,
        mode=mode,
        tp_hit_count=0,
        remaining_pct=0.0 if status == TradeStatus.CLOSED else 1.0,
        exit_last_check_at=1,
        qty_remaining=0.0 if status == TradeStatus.CLOSED else 1.0,
        realized_pnl_usd=pnl,
    )


def test_get_user_stats() -> None:
    repo = FakeTradesRepo(
        [
            _make_trade(
                trade_id="1",
                user_id="demo_user",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=20.0,
            ),
            _make_trade(
                trade_id="2",
                user_id="demo_user",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=-10.0,
            ),
            _make_trade(
                trade_id="3",
                user_id="other_user",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=50.0,
            ),
        ]
    )

    uc = GetPaperTradeStatsUseCase(repo, test_user_ids={"demo_user"})

    stats = uc.get_user_stats(
        user_id="demo_user",
        symbol="BTCUSDT",
        tf="30m",
        model_id="RSI_REBOUND_ST_124",
    )

    assert stats["scope"] == "user"
    assert stats["user_id"] == "demo_user"
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["net_profit_usd"] == 10.0
    assert stats["current_balance_usd"] == 1010.0


def test_get_test_user_stats() -> None:
    repo = FakeTradesRepo(
        [
            _make_trade(
                trade_id="1",
                user_id="demo_user",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=20.0,
            ),
            _make_trade(
                trade_id="2",
                user_id="demo_user_2",
                symbol="BTCUSDT",
                tf="1h",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=30.0,
            ),
            _make_trade(
                trade_id="3",
                user_id="user_1",
                symbol="BTCUSDT",
                tf="1h",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=40.0,
            ),
        ]
    )

    uc = GetPaperTradeStatsUseCase(repo, test_user_ids={"demo_user", "demo_user_2"})

    stats = uc.get_test_user_stats(symbol="BTCUSDT")

    assert stats["scope"] == "test_users"
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 2
    assert stats["net_profit_usd"] == 50.0
    assert stats["current_balance_usd"] == 1050.0


def test_get_global_stats() -> None:
    repo = FakeTradesRepo(
        [
            _make_trade(
                trade_id="1",
                user_id="demo_user",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=20.0,
            ),
            _make_trade(
                trade_id="2",
                user_id="user_1",
                symbol="BTCUSDT",
                tf="30m",
                model_id="RSI_REBOUND_ST_124",
                status=TradeStatus.CLOSED,
                pnl=-10.0,
            ),
            _make_trade(
                trade_id="3",
                user_id="user_2",
                symbol="ETHUSDT",
                tf="1h",
                model_id="OTHER_RULE",
                status=TradeStatus.OPEN,
                pnl=2.5,
            ),
        ]
    )

    uc = GetPaperTradeStatsUseCase(repo, test_user_ids={"demo_user"})

    stats = uc.get_global_stats(mode="sandbox")

    assert stats["scope"] == "global"
    assert stats["total_trades"] == 3
    assert stats["closed_trades"] == 2
    assert stats["open_trades"] == 1
    assert stats["net_profit_usd"] == 12.5
