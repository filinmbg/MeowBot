from __future__ import annotations

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache


def _trade(*, trade_id: str, user_id: str = "tg:1", symbol: str = "BTCUSDT", opened_at: int = 1) -> Trade:
    return Trade(
        trade_id=trade_id,
        user_id=user_id,
        symbol=symbol,
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=opened_at,
        entry_price=100.0,
        qty=1.0,
        leverage=5,
        stake_usd=10.0,
        tf_entry="15m",
        model_id="rule",
        entry_bar_close_time=opened_at,
        sl_price=98.0,
        mode="sandbox",
        tp_hit_count=0,
        remaining_pct=1.0,
        exit_last_check_at=opened_at,
        qty_remaining=1.0,
        realized_pnl_usd=0.0,
    )


def test_active_trades_cache_does_not_replace_duplicate_open_symbol() -> None:
    cache = ActiveTradesCache()
    first = _trade(trade_id="first", opened_at=1)
    second = _trade(trade_id="second", opened_at=2)

    cache.load_open_trades([first, second], source="test")

    assert cache.get_open_trade(user_id="tg:1", symbol="BTCUSDT") is first
    assert cache.count() == 1


def test_active_trades_cache_excludes_placeholder_debug_user() -> None:
    cache = ActiveTradesCache()

    cache.load_open_trades([_trade(trade_id="debug", user_id="tg:123456789")], source="test")

    assert cache.get_all_open_trades() == []
    assert cache.count() == 0
