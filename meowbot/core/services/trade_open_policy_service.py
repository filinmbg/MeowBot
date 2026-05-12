from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ActiveTradeRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    user_id: str
    symbol: str
    timeframe: str
    rule_id: str
    status: str = "open"


class TradeOpenDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    conflict_trade_id: str | None = None


class TradeOpenPolicyService:
    """
    Правила відкриття:

    1. Для звичайного юзера:
       1 відкритий трейд на монету одночасно.
       Тобто якщо є open trade по BTCUSDT, інший BTCUSDT не відкриваємо
       незалежно від timeframe чи rule_id.

    2. Для тестового юзера:
       1 відкритий трейд на монету на timeframe на rule_id.
       Тобто унікальність:
       user_id + symbol + timeframe + rule_id + status=open
    """

    def can_open_trade(
        self,
        *,
        user_id: str,
        symbol: str,
        timeframe: str,
        rule_id: str,
        is_test_user: bool,
        active_trades: list[ActiveTradeRef],
    ) -> TradeOpenDecision:
        open_trades = [
            trade
            for trade in active_trades
            if trade.status.lower() == "open"
        ]

        for trade in open_trades:
            if (
                trade.user_id == user_id
                and trade.symbol.upper() == symbol.upper()
            ):
                return TradeOpenDecision(
                    allowed=False,
                    reason="open_trade_exists_for_symbol",
                    conflict_trade_id=trade.trade_id,
                )

        return TradeOpenDecision(
            allowed=True,
            reason="allowed",
            conflict_trade_id=None,
        )
