from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import anyio
import httpx
import pytest
from pymongo.errors import NetworkTimeout

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.bot_state_cache import BotStateCache, CacheBackedBotStateRepositoryAsync
from meowbot.core.usecases.async_online_entry_cycle import AsyncOnlineEntryCycleUseCase


class FakeTradesRepoAsync:
    def __init__(self, open_trades: list[Trade], *, final_symbol_conflict: Trade | None = None) -> None:
        self.open_trades = list(open_trades)
        self.final_symbol_conflict = final_symbol_conflict
        self.created: list[Trade] = []

    async def get_open_trade_entities_by_user(self, user_id: str, *, limit: int = 200) -> list[Trade]:
        return [
            trade
            for trade in self.open_trades
            if trade.user_id == user_id
            and (trade.status.value if hasattr(trade.status, "value") else str(trade.status)) == "OPEN"
        ][:limit]

    async def get_open_trade_entity_by_user_symbol(
        self,
        user_id: str,
        symbol: str,
        *,
        mode: str | None = None,
    ) -> Trade | None:
        if self.final_symbol_conflict is not None:
            return self.final_symbol_conflict
        for trade in self.open_trades:
            status = trade.status.value if hasattr(trade.status, "value") else str(trade.status)
            if (
                trade.user_id == user_id
                and trade.symbol.upper() == symbol.upper()
                and status == "OPEN"
                and (mode is None or trade.mode == mode)
            ):
                return trade
        return None

    async def get_trades(self, *, user_id: str | None = None, mode: str | None = None, limit: int = 5000):
        return []

    async def create_trade(self, trade: Trade) -> None:
        self.created.append(trade)


class FailingHistoryTradesRepoAsync(FakeTradesRepoAsync):
    def __init__(self) -> None:
        super().__init__([])
        self.history_limits: list[int] = []

    async def get_trades(self, *, user_id: str | None = None, mode: str | None = None, limit: int = 5000):
        self.history_limits.append(limit)
        raise NetworkTimeout("temporary mongo timeout")


class NoRuntimeReadTradesRepoAsync(FakeTradesRepoAsync):
    async def get_open_trade_entities_by_user(self, user_id: str, *, limit: int = 200) -> list[Trade]:
        raise AssertionError("entry runtime must not read user open trades from Mongo when cache is enabled")

    async def get_open_trade_entity_by_user_symbol(
        self,
        user_id: str,
        symbol: str,
        *,
        mode: str | None = None,
    ) -> Trade | None:
        raise AssertionError("entry runtime must not read symbol conflict from Mongo when cache is enabled")

    async def get_trades(self, *, user_id: str | None = None, mode: str | None = None, limit: int = 5000):
        raise AssertionError("entry runtime must not read trade history from Mongo when cache is enabled")


class RacingActiveTradesCache(ActiveTradesCache):
    def __init__(self, conflict_trade: Trade) -> None:
        super().__init__()
        self.conflict_trade = conflict_trade

    def get_user_open_trades(self, user_id: str, *, mode: str | None = None) -> list[Trade]:
        return []

    def get_open_trade(self, *, user_id: str, symbol: str, mode: str | None = None) -> Trade | None:
        return self.conflict_trade


class FakeBotStateRepoAsync:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.set_values: list[tuple[str, int]] = []
        self.acquired: list[tuple[str, str, int]] = []
        self.released: list[tuple[str, str]] = []

    async def get_int(self, key: str, default: int = 0) -> int:
        return self.values.get(key, default)

    async def set_int(self, key: str, value: int) -> None:
        self.values[key] = int(value)
        self.set_values.append((key, int(value)))

    async def acquire_lock(self, *, key: str, owner: str, ttl_ms: int = 120_000) -> bool:
        self.acquired.append((key, owner, ttl_ms))
        return True

    async def release_lock(self, *, key: str, owner: str) -> None:
        self.released.append((key, owner))


class FakeTradeEventsRepoAsync:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def add_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class FakeBroker:
    def __init__(self, *, transform=None) -> None:
        self.open_calls = 0
        self.transform = transform

    async def open_position(self, trade: Trade) -> Trade:
        self.open_calls += 1
        if self.transform is not None:
            return self.transform(trade)
        return trade


class DelayedBroker(FakeBroker):
    async def open_position(self, trade: Trade) -> Trade:
        self.open_calls += 1
        await anyio.sleep(0.05)
        return trade


class FakeLiveRiskService:
    def __init__(self, result) -> None:
        self.result = result

    async def check_new_entry(self, **kwargs):
        return self.result


class FakeBarsRepoForRun:
    def __init__(self, bars: list[SimpleNamespace]) -> None:
        self.bars = bars

    async def get_tail(self, *, symbol: str, tf: str, n: int = 300, **kwargs) -> list[SimpleNamespace]:
        return self.bars[-n:]


class FakeTelegramUsersRepoAsync:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    async def list_enabled_trading_users(self) -> list[dict]:
        return list(self.users)


class FakeExchange:
    def __init__(self, mark_price: float | None = 100.0) -> None:
        self.mark_price = mark_price
        self.calls: list[str] = []

    async def get_mark_price(self, symbol: str) -> float | None:
        self.calls.append(symbol)
        return self.mark_price


class FakeDetector:
    def __init__(self, *, signal: bool = False, rule_id: str = "RSI_REBOUND_ST_124", meta: dict | None = None) -> None:
        self.signal = signal
        self.rule_id = rule_id
        self.meta = dict(meta or {})
        self.calls: list[list[int]] = []

    def check_long(self, bars: list[SimpleNamespace]) -> SimpleNamespace:
        self.calls.append([bar.close_time for bar in bars])
        return SimpleNamespace(
            signal=self.signal,
            reason="test_signal" if self.signal else "no_signal",
            current_rsi=None,
            min_prev_rsi=None,
            supertrend_bullish=None,
            rule_id=self.rule_id,
            meta=self.meta,
        )


def _make_open_trade(*, user_id: str, symbol: str, mode: str = "sandbox") -> Trade:
    return Trade(
        trade_id=f"{user_id}:{symbol}:existing",
        user_id=user_id,
        symbol=symbol,
        side=Side.LONG,
        status=TradeStatus.OPEN,
        opened_at=1,
        entry_price=100.0,
        qty=1.0,
        leverage=5,
        stake_usd=10.0,
        tf_entry="1h",
        model_id="rule-existing",
        entry_bar_close_time=1,
        sl_price=98.0,
        mode=mode,
        tp_hit_count=0,
        remaining_pct=1.0,
        exit_last_check_at=1,
        qty_remaining=1.0,
        realized_pnl_usd=0.0,
        exchange_position_mode="one_way" if mode == "live" else None,
        exchange_position_side="BOTH" if mode == "live" else None,
        exchange_position_amt=1.0 if mode == "live" else 0.0,
        exchange_stop_order={
            "algoId": "stop-1",
            "clientAlgoId": "mb-s0",
            "algoStatus": "NEW",
            "orderType": "STOP_MARKET",
        } if mode == "live" else None,
    )


def _make_feature_bar(close_time: int, close: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        close_time=close_time,
        open_time=close_time - 60_000,
        o=close,
        h=close,
        l=close,
        c=close,
        v=1.0,
        features={},
    )


def _v2_indicator_values(**overrides) -> dict:
    values = {
        "rsi_14": 82.0,
        "dist_to_ema_50_pct": 0.022,
        "volume_ratio_sma_20": 2.4,
        "atr_14_pct": 0.006,
        "vol_peak_offset_10": -1.0,
        "close_position_in_candle": 0.70,
        "adx_14": 29.0,
    }
    values.update(overrides)
    return values


def _v2_strict_meta(level: str = "strong", **overrides) -> dict:
    meta = {
        "signal_level": level,
        "signal_score": 8 if level == "strong" else 6,
        "position_size_multiplier": {"strong": 1.5, "medium": 1.25, "weak": 1.0}[level],
        "soft_stop_activation_pct": {"strong": 1.0, "medium": 0.8, "weak": 0.6}[level],
        "soft_stop_activation_trigger": "tp1_hit",
        "tp_step_pct": {"strong": 1.0, "medium": 0.8, "weak": 0.6}[level],
        "soft_stop_start_pct": 0.1,
        "soft_stop_increment_pct": 0.05,
        "soft_stop_increment_interval_seconds": 900,
        "indicator_values": _v2_indicator_values(),
        "strong_result": level == "strong",
        "medium_result": level in {"strong", "medium"},
        "weak_result": True,
        "strong_failed_conditions": [],
        "medium_failed_conditions": [],
        "weak_failed_conditions": [],
        "final_signal": level,
    }
    meta.update(overrides)
    return meta


def _make_entry_uc_for_formatting() -> AsyncOnlineEntryCycleUseCase:
    return AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun([]),
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=FakeBotStateRepoAsync(),
        trade_events_repo=FakeTradeEventsRepoAsync(),
        broker=FakeBroker(),
        exchange=FakeExchange(),
        telegram_users_repo=FakeTelegramUsersRepoAsync([]),
        features_ver="v2_core",
    )


def _allowed_live_risk_result() -> SimpleNamespace:
    return SimpleNamespace(
        allowed=True,
        warn_user=False,
        warning_code=None,
        stake_margin_usdt=10.0,
        leverage=5,
        qty=1.0,
        available_balance_usdt=100.0,
        current_margin_ratio_pct=1.0,
        symbol_max_leverage=50,
        requested_leverage=5,
        final_leverage=5,
        leverage_adjusted=False,
        required_margin_usdt=10.0,
        notional_usdt=50.0,
        base_stake_margin_usdt=10.0,
        position_size_multiplier=1.0,
        planned_qty=1.0,
        planned_notional_usdt=50.0,
        adjusted_qty=1.0,
        adjusted_notional_usdt=50.0,
        min_notional_usdt=5.0,
        qty_step=0.1,
        min_qty=0.1,
        qty_bump_applied=False,
        account_margin_used_usdt=5.0,
        account_margin_limit_usdt=100.0,
        account_margin_usage_pct=5.0,
        account_margin_current_used_usdt=5.0,
        account_margin_after_entry_usdt=15.0,
        account_margin_per_trade_limit_usdt=50.0,
    )


def test_signal_log_formats_v2_indicators_and_failed_main() -> None:
    uc = _make_entry_uc_for_formatting()
    meta = {
        "indicator_values": _v2_indicator_values(
            rsi_14=54.9123,
            dist_to_ema_50_pct=0.0019,
            volume_ratio_sma_20=0.6463,
            atr_14_pct=0.0015,
            close_position_in_candle=0.631,
            adx_14=37.04,
            vol_peak_offset_10=-3.0,
        ),
        "weak_failed_conditions": [
            "rsi_14 < 75",
            "volume_ratio_sma_20 < 1.5",
            "atr_14_pct < 0.004",
        ],
    }
    result = SimpleNamespace(signal=False, reason="v2_level_conditions_not_met")

    indicators = uc._format_signal_indicators(
        result=result,
        meta=meta,
        bar=_make_feature_bar(100_000),
        is_v2_signal=True,
    )
    failed_main = uc._format_failed_conditions(uc._signal_failed_main(result=result, meta=meta, is_v2_signal=True))

    assert "rsi=54.9" in indicators
    assert "ema_dist=0.19%" in indicators
    assert "volume=0.65x/64.63%" in indicators
    assert "atr=0.15%" in indicators
    assert "adx=37.0" in indicators
    assert failed_main == '["rsi_14 < 75", "volume_ratio_sma_20 < 1.5", "atr_14_pct < 0.004"]'


def test_signal_log_formats_v1_indicators_with_na_values() -> None:
    uc = _make_entry_uc_for_formatting()
    bar = _make_feature_bar(100_000, close=102.0)
    bar.features = {
        "rsi14": 51.234,
        "ema_50": 100.0,
        "supertrend_bullish_10_3_0": 1,
        "volume_ratio_sma_20": 1.5,
        "atr_14_pct": 0.0045,
    }
    result = SimpleNamespace(
        signal=False,
        reason="no_rebound_base",
        current_rsi=51.234,
        min_prev_rsi=46.789,
        supertrend_bullish=1,
    )

    indicators = uc._format_signal_indicators(
        result=result,
        meta={},
        bar=bar,
        is_v2_signal=False,
    )
    failed_main = uc._format_failed_conditions(uc._signal_failed_main(result=result, meta={}, is_v2_signal=False))

    assert "rsi_prev=46.8" in indicators
    assert "rsi_now=51.2" in indicators
    assert "rsi=51.2" in indicators
    assert "ema_dist=2.00%" in indicators
    assert "supertrend=bullish" in indicators
    assert "volume=1.50x/150.00%" in indicators
    assert "atr=0.45%" in indicators
    assert "adx=N/A" in indicators
    assert failed_main == '["no_rebound_base"]'


@pytest.mark.anyio
async def test_run_evaluates_entry_only_on_latest_closed_bar_after_warmup() -> None:
    symbol = "BTCUSDT"
    tf = "15m"
    bars = [
        _make_feature_bar(100_000, 100.0),
        _make_feature_bar(200_000, 101.0),
        _make_feature_bar(300_000, 102.0),
    ]
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    detector = FakeDetector(signal=False)
    exchange = FakeExchange(mark_price=102.0)

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun(bars),
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=FakeBroker(),
        exchange=exchange,
        telegram_users_repo=FakeTelegramUsersRepoAsync(
            [
                {
                    "trading_user_id": "tg:42",
                    "telegram_id": 42,
                    "trading_mode": "sandbox",
                    "allow_long": True,
                    "plan_code": "vip",
                    "features_json": {
                        "max_symbols": 150,
                        "max_open_trades_total": None,
                        "max_open_trades_per_symbol": 1,
                    },
                    "allowed_symbols": [symbol],
                    "enabled_symbols": [symbol],
                    "max_risk_trades": 5,
                }
            ]
        ),
        features_ver="v2_core",
    )
    uc.detector = detector
    cursor_key = uc._cursor_key(symbol, tf)
    bot_state_repo.values[cursor_key] = 50_000

    processed, last_close, reason = await uc.run(symbol, tf, now_ms=999_000)

    assert processed is True
    assert last_close == 300_000
    assert reason == "entry_processed"
    assert detector.calls == [[100_000, 200_000, 300_000]]
    assert exchange.calls == []
    assert bot_state_repo.values[cursor_key] == 300_000
    assert bot_state_repo.set_values[-1] == (cursor_key, 300_000)


@pytest.mark.anyio
async def test_run_initializes_cursor_without_historical_entry_check() -> None:
    symbol = "ETHUSDT"
    tf = "1h"
    bars = [
        _make_feature_bar(100_000, 100.0),
        _make_feature_bar(200_000, 101.0),
    ]
    bot_state_repo = FakeBotStateRepoAsync()
    detector = FakeDetector(signal=True)

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun(bars),
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=bot_state_repo,
        trade_events_repo=FakeTradeEventsRepoAsync(),
        broker=FakeBroker(),
        exchange=FakeExchange(mark_price=101.0),
        telegram_users_repo=FakeTelegramUsersRepoAsync([]),
        features_ver="v2_core",
    )
    uc.detector = detector
    cursor_key = uc._cursor_key(symbol, tf)

    processed, last_close, reason = await uc.run(symbol, tf, now_ms=999_000)

    assert processed is False
    assert last_close == 200_000
    assert reason == "cursor_initialized_live_mode"
    assert detector.calls == []
    assert bot_state_repo.values[cursor_key] == 200_000


@pytest.mark.anyio
async def test_strategy_routing_v1_does_not_evaluate_v18() -> None:
    symbol = "BTCUSDT"
    tf = "15m"
    bars = [
        _make_feature_bar(100_000, 100.0),
        _make_feature_bar(200_000, 101.0),
    ]
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    detector = FakeDetector(signal=False)
    v18_detector = FakeDetector(
        signal=True,
        rule_id="LONG_BREAKOUT_V18",
        meta=_v2_strict_meta("strong"),
    )
    broker = FakeBroker()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun(bars),
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=FakeExchange(mark_price=101.0),
        telegram_users_repo=FakeTelegramUsersRepoAsync(
            [
                {
                    "trading_user_id": "tg:42",
                    "telegram_id": 42,
                    "strategy_version": "v1",
                    "trading_mode": "sandbox",
                    "allow_long": True,
                    "plan_code": "vip",
                    "features_json": {
                        "max_symbols": 150,
                        "max_open_trades_total": None,
                        "max_open_trades_per_symbol": 1,
                    },
                    "allowed_symbols": [symbol],
                    "enabled_symbols": [symbol],
                    "max_risk_trades": 5,
                }
            ]
        ),
        features_ver="v2_core",
    )
    uc.detector = detector
    uc.v18_detector = v18_detector
    cursor_key = uc._cursor_key(symbol, tf)
    bot_state_repo.values[cursor_key] = 50_000

    processed, last_close, reason = await uc.run(symbol, tf, now_ms=999_000)

    assert processed is True
    assert last_close == 200_000
    assert reason == "entry_processed"
    assert detector.calls == [[100_000, 200_000]]
    assert v18_detector.calls == []
    assert broker.open_calls == 0


@pytest.mark.anyio
async def test_strategy_routing_v2_uses_v18_when_enabled() -> None:
    symbol = "BTCUSDT"
    tf = "15m"
    bars = [
        _make_feature_bar(100_000, 100.0),
        _make_feature_bar(200_000, 101.0),
    ]
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    detector = FakeDetector(signal=False)
    v18_detector = FakeDetector(
        signal=True,
        rule_id="LONG_BREAKOUT_V18",
        meta=_v2_strict_meta("strong"),
    )
    trades_repo = FakeTradesRepoAsync([])
    broker = FakeBroker()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun(bars),
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=FakeExchange(mark_price=101.0),
        telegram_users_repo=FakeTelegramUsersRepoAsync(
            [
                {
                    "trading_user_id": "tg:42",
                    "telegram_id": 42,
                    "strategy_version": "v2",
                    "trading_mode": "sandbox",
                    "allow_long": True,
                    "plan_code": "vip",
                    "features_json": {
                        "max_symbols": 150,
                        "max_open_trades_total": None,
                        "max_open_trades_per_symbol": 1,
                    },
                    "allowed_symbols": [symbol],
                    "enabled_symbols": [symbol],
                    "default_stake_mode": "percent",
                    "default_stake_value": 1.0,
                    "default_leverage": 5,
                    "sandbox_start_balance_usd": 1000.0,
                    "max_risk_trades": 5,
                }
            ]
        ),
        features_ver="v2_core",
        enable_v18_for_v2=True,
    )
    uc.detector = detector
    uc.v18_detector = v18_detector
    cursor_key = uc._cursor_key(symbol, tf)
    bot_state_repo.values[cursor_key] = 50_000

    processed, last_close, reason = await uc.run(symbol, tf, now_ms=999_000)

    assert processed is True
    assert last_close == 200_000
    assert reason == "entry_processed"
    assert detector.calls == []
    assert v18_detector.calls == [[100_000, 200_000]]
    assert broker.open_calls == 1
    assert trades_repo.created
    created = trades_repo.created[0]
    assert created.model_id == "LONG_BREAKOUT_V18"
    assert created.strategy_version == "v2"
    assert created.subscription_type == "vip"
    assert created.signal_level == "strong"
    assert created.signal_score == 8
    assert created.position_size_multiplier == 1.5
    assert created.stake_usd == 15.0
    assert created.qty == pytest.approx(0.7425742574)
    assert created.exit_profile["tp_step_pct"] == 1.0
    assert created.exit_profile["soft_stop_activation_pct"] == 1.0
    assert created.exit_profile["soft_stop_activation_trigger"] == "tp1_hit"
    assert created.exit_profile["soft_stop_start_pct"] == 0.1
    assert created.exit_profile["soft_stop_increment_pct"] == 0.05
    assert created.exit_profile["soft_stop_increment_interval_seconds"] == 900
    assert created.entry_indicators["features"]["rsi_14"] == 82.0


@pytest.mark.anyio
async def test_v2_plan_user_with_raw_v1_strategy_never_opens_legacy_trade() -> None:
    symbol = "BTCUSDT"
    tf = "15m"
    bars = [
        _make_feature_bar(100_000, 100.0),
        _make_feature_bar(200_000, 101.0),
    ]
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    detector = FakeDetector(signal=True, rule_id="RSI_REBOUND_ST_124")
    v18_detector = FakeDetector(signal=False, rule_id="LONG_BREAKOUT_V18")
    trades_repo = FakeTradesRepoAsync([])
    broker = FakeBroker()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=FakeBarsRepoForRun(bars),
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=FakeExchange(mark_price=101.0),
        telegram_users_repo=FakeTelegramUsersRepoAsync(
            [
                {
                    "trading_user_id": "tg:900012003",
                    "telegram_id": 900012003,
                    "email": "pro_test_v2@example.com",
                    "username": "pro_test_v2",
                    "strategy_version": "v1",
                    "trading_mode": "sandbox",
                    "allow_long": True,
                    "plan_code": "pro_v2",
                    "features_json": {
                        "strategy_version": "v2",
                        "enabled_strategy_rules": ["LONG_BREAKOUT_V18"],
                        "max_symbols": 100,
                        "max_open_trades_total": 20,
                        "max_open_trades_per_symbol": 1,
                    },
                    "allowed_symbols": [symbol],
                    "enabled_symbols": [symbol],
                    "max_risk_trades": 5,
                }
            ]
        ),
        features_ver="v2_core",
        enable_v18_for_v2=True,
    )
    uc.detector = detector
    uc.v18_detector = v18_detector
    bot_state_repo.values[uc._cursor_key(symbol, tf)] = 50_000

    processed, last_close, reason = await uc.run(symbol, tf, now_ms=999_000)

    assert processed is True
    assert last_close == 200_000
    assert reason == "entry_processed"
    assert detector.calls == []
    assert v18_detector.calls == [[100_000, 200_000]]
    assert broker.open_calls == 0
    assert trades_repo.created == []


@pytest.mark.anyio
async def test_v2_entry_blocks_when_adx_is_missing_before_open() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
    )
    indicator_values = _v2_indicator_values(adx_14=None)

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "strategy_version": "v2",
            "trading_mode": "sandbox",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["BTCUSDT"],
            "enabled_symbols": ["BTCUSDT"],
            "default_stake_mode": "percent",
            "default_stake_value": 1.0,
            "default_leverage": 5,
            "sandbox_start_balance_usd": 1000.0,
            "max_risk_trades": 5,
        },
        symbol="BTCUSDT",
        tf="15m",
        now_ms=999_000,
        bar=_make_feature_bar(200_000, 101.0),
        rule_id="LONG_BREAKOUT_V18",
        strategy_version="v2",
        signal_meta=_v2_strict_meta("medium", indicator_values=indicator_values, final_signal="medium"),
        signal_entry_price=101.0,
        live_entry_price=101.0,
        deviation_pct=0.0,
        open_trades_by_user={},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    skip_events = [
        event for event in trade_events_repo.events
        if event["event_type"] == "ENTRY_SKIPPED_MISSING_INDICATOR"
    ]
    assert skip_events
    assert skip_events[0]["payload"]["missing_indicators"] == ["adx_14"]
    assert skip_events[0]["payload"]["admin_reason"] == "Missing indicators: ADX"


@pytest.mark.anyio
async def test_try_open_for_user_rechecks_open_symbol_under_lock() -> None:
    user_id = "tg:42"
    existing_trade = _make_open_trade(user_id=user_id, symbol="BTCUSDT")
    trades_repo = FakeTradesRepoAsync([existing_trade])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([existing_trade], source="test")

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "sandbox",
            "allow_long": True,
            "max_open_trades_total": 5,
            "max_open_trades_per_symbol": 1,
        },
        symbol="BTCUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    assert len(bot_state_repo.acquired) == 2
    assert len(bot_state_repo.released) == 2


@pytest.mark.anyio
async def test_simultaneous_timeframes_create_only_one_open_trade_for_symbol() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = CacheBackedBotStateRepositoryAsync(
        cache=BotStateCache(),
        persistence_repo=None,
        persist_writes=False,
    )
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = DelayedBroker()
    active_trades_cache = ActiveTradesCache()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )
    base_user = {
        "trading_user_id": user_id,
        "telegram_id": 42,
        "trading_mode": "sandbox",
        "allow_long": True,
        "plan_code": "vip",
        "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
        "allowed_symbols": ["BTCUSDT"],
        "enabled_symbols": ["BTCUSDT"],
        "max_risk_trades": 5,
    }
    results = []

    async def run_for_tf(tf: str):
        result = await uc._try_open_for_user(
            tg_user=dict(base_user),
            symbol="BTCUSDT",
            tf=tf,
            now_ms=123_456,
            bar=SimpleNamespace(close_time=120_000, features={}),
            rule_id="RSI_REBOUND_ST_124",
            signal_entry_price=100.0,
            live_entry_price=100.0,
            deviation_pct=0.0,
            open_trades_by_user={user_id: []},
        )
        results.append(result)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_for_tf, "30m")
        tg.start_soon(run_for_tf, "4h")

    created = [result for result in results if result is not None]
    assert len(created) == 1
    assert broker.open_calls == 1
    assert len(trades_repo.created) == 1
    assert active_trades_cache.get_open_trade(user_id=user_id, symbol="BTCUSDT") is created[0]


@pytest.mark.anyio
async def test_fake_debug_user_never_creates_trade() -> None:
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = ActiveTradesCache()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": "tg:123456789",
            "telegram_id": 123456789,
            "trading_mode": "sandbox",
            "allow_long": True,
            "max_open_trades_total": 5,
            "max_open_trades_per_symbol": 1,
        },
        symbol="BTCUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    assert active_trades_cache.get_all_open_trades() == []


@pytest.mark.anyio
async def test_try_open_for_user_final_guard_blocks_symbol_race() -> None:
    user_id = "tg:42"
    existing_trade = _make_open_trade(user_id=user_id, symbol="BTCUSDT")
    trades_repo = FakeTradesRepoAsync([], final_symbol_conflict=existing_trade)
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = RacingActiveTradesCache(existing_trade)

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "sandbox",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["BTCUSDT"],
            "enabled_symbols": ["BTCUSDT"],
            "max_risk_trades": 5,
        },
        symbol="BTCUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    assert trade_events_repo.events[-1]["payload"]["guard"] == "final_pre_create"
    assert trade_events_repo.events[-1]["payload"]["conflict_trade_id"] == existing_trade.trade_id


@pytest.mark.anyio
async def test_try_open_for_user_skips_live_duplicate_symbol_without_broker_call() -> None:
    user_id = "tg:42"
    existing_trade = _make_open_trade(user_id=user_id, symbol="ETHUSDT", mode="live")
    trades_repo = FakeTradesRepoAsync([existing_trade])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades([existing_trade], source="test")

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        per_user_live_risk_service=FakeLiveRiskService(_allowed_live_risk_result()),
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "live",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["ETHUSDT"],
            "enabled_symbols": ["ETHUSDT"],
            "max_risk_trades": 5,
        },
        symbol="ETHUSDT",
        tf="15m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=3000.0,
        live_entry_price=3000.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    assert not [event for event in trade_events_repo.events if event["event_type"] == "LIVE_ORDER_REJECTED"]
    skip_events = [event for event in trade_events_repo.events if event["event_type"] == "LIVE_ENTRY_SKIPPED_ACTIVE_POSITION"]
    assert skip_events
    assert skip_events[0]["payload"]["existing_trade_id"] == existing_trade.trade_id
    assert skip_events[0]["payload"]["existing_trade_status"] == "OPEN"
    assert skip_events[0]["payload"]["position_amt"] == 1.0
    assert skip_events[0]["payload"]["positionSide"] == "BOTH"
    assert skip_events[0]["payload"]["open_order_count"] == 1
    assert skip_events[0]["payload"]["open_order_types"] == ["STOP_MARKET"]
    assert skip_events[0]["payload"]["new_signal_tf_entry"] == "15m"
    assert skip_events[0]["payload"]["new_signal_rule_id"] == "RSI_REBOUND_ST_124"


@pytest.mark.anyio
async def test_try_open_for_user_converts_active_exchange_position_exception_to_skip() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()

    def raise_active_position(_trade: Trade) -> Trade:
        raise RuntimeError(
            'pre_entry_cleanup_blocked_active_position:'
            'trade_id=tg:42:ETHUSDT:15m:120000:RSI_REBOUND_ST_124;'
            'symbol=ETHUSDT;'
            'position_side=BOTH;'
            'position_amt=0.001;'
            'open_order_count=1;'
            'open_orders=[{"algoId":"stop-1","orderType":"STOP_MARKET","status":"NEW"}]'
        )

    broker = FakeBroker(transform=raise_active_position)
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        per_user_live_risk_service=FakeLiveRiskService(_allowed_live_risk_result()),
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "live",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["ETHUSDT"],
            "enabled_symbols": ["ETHUSDT"],
            "default_stake_mode": "fixed",
            "default_stake_value": 10.0,
            "default_leverage": 5,
            "max_open_trades_total": 20,
            "max_open_trades_per_symbol": 1,
            "max_risk_trades": 5,
        },
        symbol="ETHUSDT",
        tf="15m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=3000.0,
        live_entry_price=3000.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is None
    assert broker.open_calls == 1
    assert trades_repo.created == []
    assert not [event for event in trade_events_repo.events if event["event_type"] == "LIVE_ORDER_REJECTED"]
    skip_events = [event for event in trade_events_repo.events if event["event_type"] == "LIVE_ENTRY_SKIPPED_ACTIVE_POSITION"]
    assert skip_events
    assert skip_events[0]["payload"]["reason"] == "exchange_active_position_already_exists"
    assert skip_events[0]["payload"]["attempted_trade_id"] == "tg:42:ETHUSDT:15m:120000:RSI_REBOUND_ST_124"
    assert skip_events[0]["payload"]["position_amt"] == "0.001"
    assert skip_events[0]["payload"]["positionSide"] == "BOTH"
    assert skip_events[0]["payload"]["open_order_count"] == 1
    assert skip_events[0]["payload"]["open_order_types"] == ["STOP_MARKET"]


@pytest.mark.anyio
async def test_try_open_for_user_emits_leverage_adjusted_event_and_payload() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    risk_result = _allowed_live_risk_result()
    risk_result.requested_leverage = 20
    risk_result.symbol_max_leverage = 10
    risk_result.final_leverage = 10
    risk_result.leverage = 10
    risk_result.leverage_adjusted = True

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        per_user_live_risk_service=FakeLiveRiskService(risk_result),
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "live",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["BTCUSDT"],
            "enabled_symbols": ["BTCUSDT"],
            "default_stake_mode": "fixed",
            "default_stake_value": 10.0,
            "default_leverage": 20,
            "max_open_trades_total": 20,
            "max_open_trades_per_symbol": 1,
            "max_risk_trades": 5,
        },
        symbol="BTCUSDT",
        tf="15m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is not None
    assert result.leverage == 10
    adjustment_event = next(event for event in trade_events_repo.events if event["event_type"] == "LEVERAGE_ADJUSTED")
    assert adjustment_event["payload"]["requested_leverage"] == 20
    assert adjustment_event["payload"]["exchange_max"] == 10
    assert adjustment_event["payload"]["final_leverage"] == 10
    opened_event = next(event for event in trade_events_repo.events if event["event_type"] == "OPENED")
    assert opened_event["payload"]["leverage_adjusted"] is True
    assert opened_event["payload"]["requested_leverage"] == 20
    assert opened_event["payload"]["final_leverage"] == 10
    assert opened_event["payload"]["leverage"] == 10


@pytest.mark.anyio
async def test_try_open_for_user_queues_opened_before_risk_warning() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    risk_result = _allowed_live_risk_result()
    risk_result.warn_user = True
    risk_result.warning_code = "margin_ratio_warning"
    risk_result.current_margin_ratio_pct = 7.5

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        per_user_live_risk_service=FakeLiveRiskService(risk_result),
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "live",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["SEIUSDT"],
            "enabled_symbols": ["SEIUSDT"],
            "default_stake_mode": "fixed",
            "default_stake_value": 10.0,
            "default_leverage": 5,
            "max_open_trades_total": 20,
            "max_open_trades_per_symbol": 1,
            "max_risk_trades": 5,
        },
        symbol="SEIUSDT",
        tf="15m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=0.2,
        live_entry_price=0.2,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is not None
    event_types = [event["event_type"] for event in trade_events_repo.events]
    assert event_types.index("OPENED") < event_types.index("ENTRY_WARNING_RISK")
    warning_event = next(event for event in trade_events_repo.events if event["event_type"] == "ENTRY_WARNING_RISK")
    assert warning_event["trade_id"] == result.trade_id
    assert warning_event["payload"]["entry_trade_id"] == result.trade_id
    assert warning_event["payload"]["entry_notification_required"] is True
    opened_event = next(event for event in trade_events_repo.events if event["event_type"] == "OPENED")
    assert opened_event["payload"]["risk_warning_pending"] is True


@pytest.mark.anyio
async def test_try_open_for_user_blocks_when_risk_limit_reached() -> None:
    user_id = "tg:77"
    trades_repo = FakeTradesRepoAsync(
        [
            _make_open_trade(user_id=user_id, symbol="BTCUSDT"),
            _make_open_trade(user_id=user_id, symbol="ETHUSDT"),
            _make_open_trade(user_id=user_id, symbol="SOLUSDT"),
            _make_open_trade(user_id=user_id, symbol="XRPUSDT"),
            _make_open_trade(user_id=user_id, symbol="ADAUSDT"),
        ]
    )
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = ActiveTradesCache()
    active_trades_cache.load_open_trades(trades_repo.open_trades, source="test")

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 77,
            "trading_mode": "sandbox",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"],
            "enabled_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"],
        },
        symbol="LINKUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is None
    assert broker.open_calls == 0
    assert trades_repo.created == []
    assert trade_events_repo.events[-1]["event_type"] == "ENTRY_BLOCKED_RISK"
    assert trade_events_repo.events[-1]["payload"]["reason"] == "risk_limit_reached"


@pytest.mark.anyio
async def test_try_open_for_user_uses_empty_history_without_mongo_trade_read() -> None:
    user_id = "tg:88"
    trades_repo = FailingHistoryTradesRepoAsync()
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 88,
            "trading_mode": "sandbox",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["LINKUSDT"],
            "enabled_symbols": ["LINKUSDT"],
            "default_stake_mode": "percent",
            "default_stake_value": 1.0,
            "default_leverage": 5,
            "sandbox_start_balance_usd": 1000.0,
            "max_risk_trades": 5,
        },
        symbol="LINKUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is not None
    assert broker.open_calls == 1
    assert trades_repo.created
    assert trades_repo.history_limits == []


@pytest.mark.anyio
async def test_try_open_for_user_uses_memory_cache_without_mongo_trade_reads() -> None:
    user_id = "tg:89"
    trades_repo = NoRuntimeReadTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker()
    active_trades_cache = ActiveTradesCache()

    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        active_trades_cache=active_trades_cache,
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 89,
            "trading_mode": "sandbox",
            "allow_long": True,
            "plan_code": "vip",
            "features_json": {"max_symbols": 150, "max_open_trades_total": None, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["LINKUSDT"],
            "enabled_symbols": ["LINKUSDT"],
            "default_stake_mode": "percent",
            "default_stake_value": 1.0,
            "default_leverage": 5,
            "sandbox_start_balance_usd": 1000.0,
            "max_risk_trades": 5,
        },
        symbol="LINKUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is not None
    assert broker.open_calls == 1
    assert trades_repo.created
    assert active_trades_cache.get_open_trade(user_id=user_id, symbol="LINKUSDT", mode="sandbox") is result


def test_long_entry_price_rule_allows_two_percent_downward_gap() -> None:
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=FakeBotStateRepoAsync(),
        trade_events_repo=FakeTradeEventsRepoAsync(),
        broker=FakeBroker(),
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
    )

    allowed = uc._check_long_entry_price(signal_entry_price=100.0, live_entry_price=98.01)
    blocked = uc._check_long_entry_price(signal_entry_price=100.0, live_entry_price=97.99)

    assert allowed["allowed"] is True
    assert allowed["max_downward_deviation_pct"] == 2.0
    assert allowed["entry_price_rule_id"] == "LONG_ENTRY_ASYM_UP_0_5_DOWN_2_0_V2"
    assert blocked["allowed"] is False
    assert blocked["reason"] == "live_price_too_low_for_long"


def test_signal_debug_payload_recomputes_ema_distance_from_close_and_ema50() -> None:
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=FakeBotStateRepoAsync(),
        trade_events_repo=FakeTradeEventsRepoAsync(),
        broker=FakeBroker(),
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
    )
    bar = SimpleNamespace(
        open_time=1,
        close_time=2,
        o=100.0,
        h=106.0,
        l=99.0,
        c=105.0,
        v=1000.0,
        features={
            "ema50": 100.0,
            "dist_to_ema_50_pct": 1.05,
            "rsi14": 53.0352,
            "atr14_pct": 0.01074,
            "relative_volume20": 0.0401,
        },
    )
    entry_indicators = uc._build_entry_indicators(
        bar=bar,
        symbol="ETHUSDT",
        tf="15m",
        rule_id="LONG_BREAKOUT_V18",
        strategy_version="v2",
        signal_entry_price=105.0,
        live_entry_price=105.0,
        deviation_pct=0.0,
    )

    payload = uc._build_signal_debug_payload(entry_indicators)

    assert payload["dist_to_ema_50_pct"] == pytest.approx(0.05)
    assert payload["ema_50"] == 100.0
    assert payload["close_price"] == 105.0
    assert payload["trigger_explanation"] == [
        "RSI crossed threshold",
        "Volume spike",
        "Trend confirmed",
    ]


def test_exception_payload_sanitizes_binance_signature_and_keeps_exchange_message() -> None:
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=FakeTradesRepoAsync([]),
        bot_state_repo=FakeBotStateRepoAsync(),
        trade_events_repo=FakeTradeEventsRepoAsync(),
        broker=FakeBroker(),
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
    )
    request = httpx.Request(
        "POST",
        "https://fapi.binance.com/fapi/v1/order?symbol=INJUSDT&signature=secret-signature",
    )
    response = httpx.Response(
        400,
        json={"code": -4164, "msg": "Order's notional must be no smaller than 5."},
        request=request,
    )
    exc = httpx.HTTPStatusError("bad request", request=request, response=response)

    payload = uc._build_exception_payload(exc, tf="2h", rule_id="RSI_REBOUND_ST_124")

    assert payload["http_status"] == 400
    assert payload["http_path"] == "/fapi/v1/order"
    assert payload["exchange_error_code"] == -4164
    assert payload["exchange_error_message"] == "Order's notional must be no smaller than 5."
    assert "secret-signature" not in str(payload)


@pytest.mark.anyio
async def test_try_open_for_user_emits_live_position_unprotected_event() -> None:
    user_id = "tg:42"
    trades_repo = FakeTradesRepoAsync([])
    bot_state_repo = FakeBotStateRepoAsync()
    trade_events_repo = FakeTradeEventsRepoAsync()
    broker = FakeBroker(
        transform=lambda trade: replace(
            trade,
            mode="live",
            exchange_name="binance",
            exchange_entry_order_id="12345",
            exchange_entry_status="FILLED",
            protection_status="unprotected",
            protection_error="stop_creation_failed:Precision is over the maximum defined for this asset.",
            protection_details={
                "actual_position_amt": 1.0,
                "exchange_error_code": -1111,
                "exchange_error_message": "Precision is over the maximum defined for this asset.",
            },
        )
    )
    risk_result = SimpleNamespace(
        allowed=True,
        warn_user=False,
        warning_code=None,
        stake_margin_usdt=10.0,
        leverage=5,
        qty=1.0,
        available_balance_usdt=100.0,
        current_margin_ratio_pct=1.0,
        symbol_max_leverage=50,
        requested_leverage=5,
        final_leverage=5,
        leverage_adjusted=False,
        required_margin_usdt=10.0,
        notional_usdt=50.0,
        planned_qty=1.0,
        planned_notional_usdt=50.0,
        adjusted_qty=1.0,
        adjusted_notional_usdt=50.0,
        min_notional_usdt=5.0,
        qty_step=0.1,
        min_qty=0.1,
        qty_bump_applied=False,
        account_margin_used_usdt=5.0,
        account_margin_limit_usdt=100.0,
        account_margin_usage_pct=5.0,
        account_margin_current_used_usdt=5.0,
        account_margin_after_entry_usdt=15.0,
        account_margin_per_trade_limit_usdt=50.0,
    )
    uc = AsyncOnlineEntryCycleUseCase(
        bars_repo=None,
        trades_repo=trades_repo,
        bot_state_repo=bot_state_repo,
        trade_events_repo=trade_events_repo,
        broker=broker,
        exchange=None,
        telegram_users_repo=None,
        features_ver="v2_core",
        per_user_live_risk_service=FakeLiveRiskService(risk_result),
    )

    result = await uc._try_open_for_user(
        tg_user={
            "trading_user_id": user_id,
            "telegram_id": 42,
            "trading_mode": "live",
            "allow_long": True,
            "plan_code": "pro",
            "features_json": {"max_symbols": 100, "max_open_trades_total": 20, "max_open_trades_per_symbol": 1},
            "allowed_symbols": ["BTCUSDT"],
            "enabled_symbols": ["BTCUSDT"],
            "default_stake_mode": "fixed",
            "default_stake_value": 10.0,
            "default_leverage": 5,
            "max_open_trades_total": 20,
            "max_open_trades_per_symbol": 1,
            "max_risk_trades": 5,
        },
        symbol="BTCUSDT",
        tf="30m",
        now_ms=123_456,
        bar=SimpleNamespace(close_time=120_000, features={}),
        rule_id="RSI_REBOUND_ST_124",
        signal_entry_price=100.0,
        live_entry_price=100.0,
        deviation_pct=0.0,
        open_trades_by_user={user_id: []},
    )

    assert result is not None
    assert broker.open_calls == 1
    assert trades_repo.created
    event_types = [event["event_type"] for event in trade_events_repo.events]
    assert "LIVE_POSITION_UNPROTECTED" in event_types
    opened_event = next(event for event in trade_events_repo.events if event["event_type"] == "OPENED")
    assert opened_event["payload"]["protection_status"] == "unprotected"
    assert opened_event["payload"]["protection_error"].startswith("stop_creation_failed:")
