from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SupportedTimeframe = Literal["15m", "30m", "1h", "2h", "4h"]
SupportedSide = Literal["LONG", "SHORT"]
SupportedStrategyType = Literal["rsi_rebound_supertrend"]


class RuleRiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    stake_pct: float = Field(default=0.01, gt=0)
    leverage: float = Field(default=10.0, gt=0)

    fee_rate: float = Field(default=0.0004, ge=0)
    sl_pct: float = Field(default=0.02, gt=0)

    tp_step_pct: float = Field(default=0.005, gt=0)
    tp_partial_close_pct: float = Field(default=0.25, gt=0, le=1)

    sl_trail_step_pct: float = Field(default=0.0002, ge=0)
    trail_every_ms: int = Field(default=15 * 60 * 1000, gt=0)
    loss_cooldown_ms: int = Field(default=3 * 60 * 60 * 1000, ge=0)

    max_open_trades_per_symbol: int = Field(default=1, gt=0)


class RsiReboundSupertrendParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    low_level: int = Field(..., ge=0, le=100)
    reclaim_level: int = Field(..., ge=0, le=100)
    lookback: int = Field(..., ge=2)

    @field_validator("reclaim_level")
    @classmethod
    def validate_reclaim_level(cls, value: int, info) -> int:
        low_level = info.data.get("low_level")
        if low_level is not None and value <= low_level:
            raise ValueError("reclaim_level must be greater than low_level")
        return value


class TradingRuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(..., min_length=3)
    name: str = Field(..., min_length=3)
    enabled: bool = True

    strategy_type: SupportedStrategyType
    side: SupportedSide

    allowed_timeframes: tuple[SupportedTimeframe, ...]
    symbol_whitelist: tuple[str, ...] = ()

    priority: int = 100

    test_user_enabled: bool = True
    regular_user_enabled: bool = False

    params: RsiReboundSupertrendParams
    risk: RuleRiskConfig = Field(default_factory=RuleRiskConfig)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_timeframes")
    @classmethod
    def validate_timeframes(
        cls,
        value: tuple[SupportedTimeframe, ...],
    ) -> tuple[SupportedTimeframe, ...]:
        if not value:
            raise ValueError("allowed_timeframes must not be empty")
        return value

    def supports_symbol(self, symbol: str) -> bool:
        if not self.symbol_whitelist:
            return True
        return symbol.upper() in {item.upper() for item in self.symbol_whitelist}

    def supports_timeframe(self, timeframe: str) -> bool:
        return timeframe in self.allowed_timeframes

    def is_enabled_for_user(self, *, is_test_user: bool) -> bool:
        if not self.enabled:
            return False
        if is_test_user:
            return self.test_user_enabled
        return self.regular_user_enabled


DEFAULT_PAPER_TRADING_RULES: tuple[TradingRuleConfig, ...] = (
    TradingRuleConfig(
        rule_id="RSI_REBOUND_ST_124",
        name="RSI rebound + SuperTrend (45/50/10)",
        enabled=True,
        strategy_type="rsi_rebound_supertrend",
        side="LONG",
        allowed_timeframes=("15m", "30m", "1h", "2h", "4h"),
        symbol_whitelist=("BTCUSDT",),
        priority=10,
        test_user_enabled=True,
        regular_user_enabled=False,
        params=RsiReboundSupertrendParams(
            low_level=45,
            reclaim_level=50,
            lookback=10,
        ),
        risk=RuleRiskConfig(
            stake_pct=0.01,
            leverage=10.0,
            fee_rate=0.0004,
            sl_pct=0.02,
            tp_step_pct=0.005,
            tp_partial_close_pct=0.25,
            sl_trail_step_pct=0.0002,
            trail_every_ms=15 * 60 * 1000,
            loss_cooldown_ms=3 * 60 * 60 * 1000,
            max_open_trades_per_symbol=1,
        ),
    ),
)


def get_enabled_rules_for_context(
    *,
    symbol: str,
    timeframe: str,
    is_test_user: bool,
    rules: tuple[TradingRuleConfig, ...] | None = None,
) -> list[TradingRuleConfig]:
    source = rules or DEFAULT_PAPER_TRADING_RULES

    matched = [
        rule
        for rule in source
        if rule.is_enabled_for_user(is_test_user=is_test_user)
        and rule.supports_symbol(symbol)
        and rule.supports_timeframe(timeframe)
    ]
    matched.sort(key=lambda item: (item.priority, item.rule_id))
    return matched


def get_rule_by_id(
    rule_id: str,
    rules: tuple[TradingRuleConfig, ...] | None = None,
) -> TradingRuleConfig | None:
    source = rules or DEFAULT_PAPER_TRADING_RULES
    for rule in source:
        if rule.rule_id == rule_id:
            return rule
    return None