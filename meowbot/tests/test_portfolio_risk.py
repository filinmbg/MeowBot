from meowbot.core.domain.types import Signal
from meowbot.core.domain.enums import SignalAction, Side, TradeStatus
from meowbot.core.services.execution.entry.portfolio_gate import PortfolioGate
from meowbot.core.services.execution.risk.limits import RiskLimits
from meowbot.core.services.execution.risk.sizing import PositionSizing
from meowbot.infra.memory.trades_repo import InMemoryTradesRepo
from meowbot.core.domain.types import Trade


def make_open_trade(trade_id: str, symbol: str, close_time: int = 0) -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id="u1",
        mode="sandbox",
        symbol=symbol,
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=0,
        entry_price=100.0,
        qty=1.0,
        leverage=20,
        stake_usd=1.0,
        tf_entry="15m",
        model_id="x",
        entry_bar_close_time=close_time,
        sl_price=90.0,
        exit_last_check_at=0,
    )


def test_portfolio_gate_blocks_when_max_total_reached():
    repo = InMemoryTradesRepo()
    repo.create_trade(make_open_trade("t1", "BTCUSDT"))
    repo.create_trade(make_open_trade("t2", "ETHUSDT"))

    gate = PortfolioGate(repo, limits=RiskLimits(max_open_trades_total=2, max_open_trades_per_symbol=1))
    ok = gate.allow("SOLUSDT", Signal(action=SignalAction.LONG, score=1.0))
    assert ok is False


def test_portfolio_gate_blocks_second_trade_same_symbol():
    repo = InMemoryTradesRepo()
    repo.create_trade(make_open_trade("t1", "BTCUSDT"))

    gate = PortfolioGate(repo, limits=RiskLimits(max_open_trades_total=10, max_open_trades_per_symbol=1))
    ok = gate.allow("BTCUSDT", Signal(action=SignalAction.LONG, score=1.0))
    assert ok is False


def test_position_sizing_qty():
    sizing = PositionSizing(stake_pct=0.01, leverage=20)
    equity = 100.0
    price = 200.0

    # stake=1$, notional=20$, qty=0.1
    assert abs(sizing.stake_usd(equity) - 1.0) < 1e-9
    assert abs(sizing.qty_from_price(equity, price) - 0.1) < 1e-9