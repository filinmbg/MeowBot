from __future__ import annotations

import logging
from typing import List

from meowbot.core.configs.sandbox_trading import DEFAULT_SANDBOX_TRADING_CONFIG
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
from meowbot.core.services.entry.entry_policy_service import (
    EntryPolicyDecision,
    EntryPolicyService,
)
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import (
    DetectorResult,
    RsiReboundSupertrendDetector,
    RsiReboundSupertrendConfig,
)
from meowbot.core.services.entry.sandbox_position_sizing_service import (
    SandboxPositionSizingService,
)

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

        self.detector = RsiReboundSupertrendDetector(
            RsiReboundSupertrendConfig()
        )
        self.entry_policy = EntryPolicyService(test_user_ids={"demo_user"})
        self.sizing_service = SandboxPositionSizingService(
            DEFAULT_SANDBOX_TRADING_CONFIG
        )

    def _cursor_key(self, symbol: str, tf: str) -> str:
        return f"entry_cursor:{symbol}:{tf}:{self.features_ver}"

    def _get_last_exchange_close(self, symbol: str, tf: str) -> int | None:
        last_ex = self.exchange.get_last_closed_time(symbol, tf)
        if last_ex is None:
            log.info("[entry] %s %s: no last_ex", symbol, tf)
        return last_ex

    def _initialize_cursor_if_needed(
        self,
        *,
        symbol: str,
        tf: str,
        cursor_key: str,
        last_ex: int,
    ) -> bool:
        last_decision = self.bot_state_repo.get_int(cursor_key, default=0)
        if last_decision != 0:
            return False

        self.bot_state_repo.set_int(cursor_key, last_ex)
        log.info(
            "[entry] %s %s: cursor initialized to current last_ex=%s (live mode, no replay)",
            symbol,
            tf,
            last_ex,
        )
        return True

    def _get_last_decision(self, cursor_key: str) -> int:
        return self.bot_state_repo.get_int(cursor_key, default=0)

    def _has_new_closed_bars(
        self,
        *,
        symbol: str,
        tf: str,
        last_ex: int,
        last_decision: int,
    ) -> bool:
        if last_ex <= last_decision:
            log.debug(
                "[entry] %s %s: no new closed bar (last_ex=%s, cursor=%s)",
                symbol,
                tf,
                last_ex,
                last_decision,
            )
            return False

        log.info(
            "[entry] %s %s: NEW closed bar(s) detected (last_ex=%s > cursor=%s)",
            symbol,
            tf,
            last_ex,
            last_decision,
        )
        return True

    def _load_feature_ready_bars(self, symbol: str, tf: str) -> list[Bar]:
        feature_bars = self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=250,
            features_ver=self.features_ver,
            require_features_ok=True,
        )
        if not feature_bars:
            log.info("[entry] %s %s: no feature-ready bars", symbol, tf)
            return []

        return sorted(feature_bars, key=lambda item: item.close_time)

    def _select_new_bars(
        self,
        *,
        symbol: str,
        tf: str,
        feature_bars: list[Bar],
        last_decision: int,
    ) -> list[Bar]:
        new_bars = [b for b in feature_bars if b.close_time > last_decision]
        new_bars.sort(key=lambda x: x.close_time)

        log.info(
            "[entry] %s %s: %d new feature-ready bars to evaluate",
            symbol,
            tf,
            len(new_bars),
        )
        return new_bars

    def _build_window(self, feature_bars: list[Bar], current_bar: Bar) -> list[Bar]:
        return [b for b in feature_bars if b.close_time <= current_bar.close_time]

    def _log_detector_result(
        self,
        *,
        symbol: str,
        tf: str,
        bar: Bar,
        result: DetectorResult,
    ) -> None:
        log.info(
            "[entry] %s %s: bar_close=%s signal=%s reason=%s rsi=%s min_prev_rsi=%s st=%s",
            symbol,
            tf,
            bar.close_time,
            result.signal,
            result.reason,
            f"{result.current_rsi:.2f}" if result.current_rsi is not None else "None",
            f"{result.min_prev_rsi:.2f}" if result.min_prev_rsi is not None else "None",
            result.supertrend_bullish,
        )

    def _advance_cursor(
        self,
        *,
        symbol: str,
        tf: str,
        cursor_key: str,
        close_time: int,
        reason: str,
    ) -> None:
        self.bot_state_repo.set_int(cursor_key, close_time)
        log.info(
            "[entry] %s %s: %s, cursor updated to %s",
            symbol,
            tf,
            reason,
            close_time,
        )

    def _gate_allows_long(self, symbol: str) -> bool:
        dummy_signal = type("DummySignal", (), {"action": SignalAction.LONG})()
        return self.gate.allow(symbol, dummy_signal)

    def _build_trade(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        now_ms: int,
        bar: Bar,
        result: DetectorResult,
    ) -> Trade:
        all_trades = self.trades_repo.get_open_trades()
        try:
            all_trades = list(all_trades)
        except TypeError:
            all_trades = []

        # Для sizing потрібні і закриті, і відкриті.
        # Якщо repo пізніше отримає get_all_trades / get_trades_by_user — підключимо.
        sizing = self.sizing_service.calculate(
            user_id=user_id,
            entry_price=float(bar.c),
            trades=all_trades,
        )

        entry_price = float(bar.c)
        sl_price = entry_price * 0.98

        trade_id = f"{user_id}:{symbol}:{tf}:{bar.close_time}:{result.rule_id}"
        model_id = result.rule_id

        return Trade(
            trade_id=trade_id,
            user_id=user_id,
            symbol=symbol,
            side=Side.LONG,
            status=TradeStatus.OPEN,
            opened_at=now_ms,
            entry_price=entry_price,
            qty=sizing.qty,
            leverage=int(sizing.leverage),
            stake_usd=sizing.stake_usd,
            tf_entry=tf,
            model_id=model_id,
            entry_bar_close_time=bar.close_time,
            sl_price=sl_price,
            mode="sandbox",
            tp_hit_count=0,
            remaining_pct=1.0,
            exit_last_check_at=now_ms,
            qty_remaining=sizing.qty,
            realized_pnl_usd=0.0,
        )

    def _build_open_event_payload(
        self,
        *,
        trade: Trade,
        bar: Bar,
        result: DetectorResult,
        policy_decision: EntryPolicyDecision,
    ) -> dict:
        features = bar.features or {}
        return {
            "entry_price": trade.entry_price,
            "qty": trade.qty,
            "stake_usd": trade.stake_usd,
            "sl_price": trade.sl_price,
            "side": trade.side.value if hasattr(trade.side, "value") else str(trade.side),
            "tf_entry": trade.tf_entry,
            "model_id": trade.model_id,
            "features_ver": self.features_ver,
            "rule_id": result.rule_id,
            "detector_reason": result.reason,
            "policy_reason": policy_decision.reason,
            "sandbox_start_balance_usd": DEFAULT_SANDBOX_TRADING_CONFIG.starting_balance_usd,
            "sandbox_entry_mode": DEFAULT_SANDBOX_TRADING_CONFIG.entry_mode,
            "sandbox_entry_percent": DEFAULT_SANDBOX_TRADING_CONFIG.entry_percent,
            "sandbox_entry_fixed_usd": DEFAULT_SANDBOX_TRADING_CONFIG.entry_fixed_usd,
            "rsi14": features.get("rsi14"),
            "supertrend_bullish_10_3_0": features.get("supertrend_bullish_10_3_0"),
            "min_prev_rsi": result.min_prev_rsi,
            "current_rsi": result.current_rsi,
            "supertrend_bullish": result.supertrend_bullish,
            "detector_meta": result.meta,
        }

    def _get_user_id_for_entry(self) -> str:
        return "demo_user"

    def _check_entry_policy(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        rule_id: str,
    ) -> EntryPolicyDecision:
        open_trades = self.trades_repo.get_open_trades()
        return self.entry_policy.can_open_trade(
            user_id=user_id,
            symbol=symbol,
            tf=tf,
            rule_id=rule_id,
            open_trades=open_trades,
        )

    def _create_trade_and_event(
        self,
        *,
        user_id: str,
        symbol: str,
        tf: str,
        now_ms: int,
        bar: Bar,
        result: DetectorResult,
        policy_decision: EntryPolicyDecision,
    ) -> None:
        trade = self._build_trade(
            user_id=user_id,
            symbol=symbol,
            tf=tf,
            now_ms=now_ms,
            bar=bar,
            result=result,
        )

        trade = self.broker.open_position(trade)
        self.trades_repo.create_trade(trade)

        self.trade_events_repo.add_event(
            trade_id=trade.trade_id,
            event_type="OPENED",
            ts=now_ms,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload=self._build_open_event_payload(
                trade=trade,
                bar=bar,
                result=result,
                policy_decision=policy_decision,
            ),
        )

        log.info(
            "[entry] %s %s: CREATED trade_id=%s entry=%s stake=%s qty=%s sl=%s mode=%s model_id=%s",
            symbol,
            tf,
            trade.trade_id,
            trade.entry_price,
            trade.stake_usd,
            trade.qty,
            trade.sl_price,
            trade.mode,
            trade.model_id,
        )

    def _process_bar(
        self,
        *,
        symbol: str,
        tf: str,
        now_ms: int,
        cursor_key: str,
        feature_bars: list[Bar],
        bar: Bar,
    ) -> None:
        window = self._build_window(feature_bars, bar)
        result = self.detector.check_long(window)

        self._log_detector_result(
            symbol=symbol,
            tf=tf,
            bar=bar,
            result=result,
        )

        if not result.signal:
            self._advance_cursor(
                symbol=symbol,
                tf=tf,
                cursor_key=cursor_key,
                close_time=bar.close_time,
                reason="no entry signal",
            )
            return

        if not self._gate_allows_long(symbol):
            log.info(
                "[entry] %s %s: gate blocked at bar_close=%s rule_id=%s",
                symbol,
                tf,
                bar.close_time,
                result.rule_id,
            )
            self._advance_cursor(
                symbol=symbol,
                tf=tf,
                cursor_key=cursor_key,
                close_time=bar.close_time,
                reason="gate blocked",
            )
            return

        user_id = self._get_user_id_for_entry()
        policy_decision = self._check_entry_policy(
            user_id=user_id,
            symbol=symbol,
            tf=tf,
            rule_id=result.rule_id,
        )

        if not policy_decision.allowed:
            log.info(
                "[entry] %s %s: policy blocked at bar_close=%s rule_id=%s reason=%s conflict_trade_id=%s",
                symbol,
                tf,
                bar.close_time,
                result.rule_id,
                policy_decision.reason,
                policy_decision.conflict_trade_id,
            )
            self._advance_cursor(
                symbol=symbol,
                tf=tf,
                cursor_key=cursor_key,
                close_time=bar.close_time,
                reason="policy blocked",
            )
            return

        try:
            self._create_trade_and_event(
                user_id=user_id,
                symbol=symbol,
                tf=tf,
                now_ms=now_ms,
                bar=bar,
                result=result,
                policy_decision=policy_decision,
            )
        except Exception as e:
            log.exception(
                "[entry] %s %s: trade not created (%s)",
                symbol,
                tf,
                type(e).__name__,
            )
        finally:
            self._advance_cursor(
                symbol=symbol,
                tf=tf,
                cursor_key=cursor_key,
                close_time=bar.close_time,
                reason="trade processed",
            )

    def run(self, symbol: str, tf: str, now_ms: int) -> None:
        last_ex = self._get_last_exchange_close(symbol, tf)
        if last_ex is None:
            return

        cursor_key = self._cursor_key(symbol, tf)

        if self._initialize_cursor_if_needed(
            symbol=symbol,
            tf=tf,
            cursor_key=cursor_key,
            last_ex=last_ex,
        ):
            return

        last_decision = self._get_last_decision(cursor_key)
        if not self._has_new_closed_bars(
            symbol=symbol,
            tf=tf,
            last_ex=last_ex,
            last_decision=last_decision,
        ):
            return

        feature_bars = self._load_feature_ready_bars(symbol, tf)
        if not feature_bars:
            return

        new_bars = self._select_new_bars(
            symbol=symbol,
            tf=tf,
            feature_bars=feature_bars,
            last_decision=last_decision,
        )
        if not new_bars:
            return

        for bar in new_bars:
            self._process_bar(
                symbol=symbol,
                tf=tf,
                now_ms=now_ms,
                cursor_key=cursor_key,
                feature_bars=feature_bars,
                bar=bar,
            )