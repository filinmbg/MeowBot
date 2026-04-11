from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meowbot.core.configs.paper_trading_rules import (
    TradingRuleConfig,
    get_enabled_rules_for_context,
)


class ClosedBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    open_time: int
    close_time: int

    open: float
    high: float
    low: float
    close: float

    rsi14: float
    supertrend_bullish: int | bool

    volume: float | None = None

    @property
    def is_supertrend_bullish(self) -> bool:
        return bool(self.supertrend_bullish)


class SignalCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    symbol: str
    timeframe: str

    rule_id: str
    rule_name: str
    strategy_type: str
    side: str

    entry_time: int
    entry_price: float

    signal_bar_close_time: int
    signal_bar_rsi14: float
    rolling_min_prev_rsi14: float

    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def signal_key(self) -> str:
        return (
            f"{self.user_id}:{self.symbol}:{self.timeframe}:"
            f"{self.rule_id}:{self.signal_bar_close_time}"
        )


class SignalDetectorService:
    """
    Працює лише по вже ЗАКРИТИХ барах.

    Для RSI rebound + SuperTrend:
    - supertrend_bullish == 1
    - min(rsi14 попередніх lookback барів) <= low_level
    - rsi14 поточного бару >= reclaim_level

    Вхідна ціна для paper/open signal = close поточного сигнального бару,
    щоб максимально відповідати бектесту.
    """

    def detect_signals(
        self,
        *,
        user_id: str,
        symbol: str,
        timeframe: str,
        bars: list[ClosedBar],
        is_test_user: bool,
        rules: tuple[TradingRuleConfig, ...] | None = None,
    ) -> list[SignalCandidate]:
        if len(bars) < 2:
            return []

        active_rules = get_enabled_rules_for_context(
            symbol=symbol,
            timeframe=timeframe,
            is_test_user=is_test_user,
            rules=rules,
        )
        if not active_rules:
            return []

        bars_sorted = sorted(bars, key=lambda item: item.close_time)
        current_bar = bars_sorted[-1]

        signals: list[SignalCandidate] = []

        for rule in active_rules:
            if rule.strategy_type != "rsi_rebound_supertrend":
                continue

            signal = self._detect_rsi_rebound_supertrend(
                user_id=user_id,
                symbol=symbol,
                timeframe=timeframe,
                bars=bars_sorted,
                current_bar=current_bar,
                rule=rule,
            )
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda item: (item.timeframe, item.rule_id))
        return signals

    def _detect_rsi_rebound_supertrend(
        self,
        *,
        user_id: str,
        symbol: str,
        timeframe: str,
        bars: list[ClosedBar],
        current_bar: ClosedBar,
        rule: TradingRuleConfig,
    ) -> SignalCandidate | None:
        lookback = rule.params.lookback
        if len(bars) < lookback + 1:
            return None

        previous_bars = bars[-(lookback + 1):-1]
        if len(previous_bars) != lookback:
            return None

        rolling_min_prev_rsi14 = min(bar.rsi14 for bar in previous_bars)

        if not current_bar.is_supertrend_bullish:
            return None

        if rolling_min_prev_rsi14 > rule.params.low_level:
            return None

        if current_bar.rsi14 < rule.params.reclaim_level:
            return None

        return SignalCandidate(
            user_id=user_id,
            symbol=symbol,
            timeframe=timeframe,
            rule_id=rule.rule_id,
            rule_name=rule.name,
            strategy_type=rule.strategy_type,
            side=rule.side,
            entry_time=current_bar.close_time,
            entry_price=current_bar.close,
            signal_bar_close_time=current_bar.close_time,
            signal_bar_rsi14=current_bar.rsi14,
            rolling_min_prev_rsi14=rolling_min_prev_rsi14,
            meta={
                "low_level": rule.params.low_level,
                "reclaim_level": rule.params.reclaim_level,
                "lookback": rule.params.lookback,
                "supertrend_bullish": current_bar.is_supertrend_bullish,
            },
        )

    def detect_signals_for_many_timeframes(
        self,
        *,
        user_id: str,
        symbol: str,
        bars_by_timeframe: dict[str, list[ClosedBar]],
        is_test_user: bool,
        rules: tuple[TradingRuleConfig, ...] | None = None,
    ) -> list[SignalCandidate]:
        signals: list[SignalCandidate] = []

        for timeframe in ("15m", "30m", "1h", "2h", "4h"):
            timeframe_bars = bars_by_timeframe.get(timeframe, [])
            if not timeframe_bars:
                continue

            signals.extend(
                self.detect_signals(
                    user_id=user_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    bars=timeframe_bars,
                    is_test_user=is_test_user,
                    rules=rules,
                )
            )

        signals.sort(
            key=lambda item: (
                item.signal_bar_close_time,
                item.timeframe,
                item.rule_id,
            )
        )
        return signals