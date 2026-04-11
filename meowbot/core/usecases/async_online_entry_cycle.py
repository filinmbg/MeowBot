from __future__ import annotations

import logging
from collections import defaultdict

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.entry.entry_policy_service import EntryPolicyService
from meowbot.core.services.entry.rsi_rebound_supertrend_detector import (
    RsiReboundSupertrendConfig,
    RsiReboundSupertrendDetector,
)


log = logging.getLogger("meowbot")


class AsyncOnlineEntryCycleUseCase:
    def __init__(
        self,
        *,
        bars_repo,
        trades_repo,
        bot_state_repo,
        trade_events_repo,
        broker,
        exchange,
        telegram_users_repo,
        features_ver: str,
        max_entry_price_deviation_pct: float = 0.5,
        sandbox_start_balance_usd: float = 1000.0,
        cooldowns_repo=None,
    ) -> None:
        self.bars_repo = bars_repo
        self.trades_repo = trades_repo
        self.bot_state_repo = bot_state_repo
        self.trade_events_repo = trade_events_repo
        self.broker = broker
        self.exchange = exchange
        self.telegram_users_repo = telegram_users_repo
        self.features_ver = features_ver
        self.max_entry_price_deviation_pct = max_entry_price_deviation_pct
        self.sandbox_start_balance_usd = sandbox_start_balance_usd
        self.cooldowns_repo = cooldowns_repo

        self.detector = RsiReboundSupertrendDetector(
            RsiReboundSupertrendConfig()
        )
        self.entry_policy = EntryPolicyService(test_user_ids=set())

    def _cursor_key(self, symbol: str, tf: str) -> str:
        return f"entry_cursor:{symbol}:{tf}:{self.features_ver}"

    async def run(self, symbol: str, tf: str, now_ms: int) -> tuple[bool, int | None, str]:
        cursor_key = self._cursor_key(symbol, tf)
        last_decision = await self.bot_state_repo.get_int(cursor_key, default=0)

        feature_bars = await self.bars_repo.get_tail(
            symbol=symbol,
            tf=tf,
            n=300,
            features_ver=self.features_ver,
            require_features_ok=False,
        )

        if not feature_bars:
            return False, None, "no_feature_ready_bars"

        latest_bar = feature_bars[-1]

        log.info(
            "[entry-async][debug] %s %s: tail_size=%s latest_close=%s last_decision=%s",
            symbol,
            tf,
            len(feature_bars),
            latest_bar.close_time,
            last_decision,
        )

        if last_decision == 0:
            await self.bot_state_repo.set_int(cursor_key, latest_bar.close_time)
            log.info(
                "[entry-async] %s %s: cursor initialized to current last feature bar close=%s (live mode, no replay)",
                symbol,
                tf,
                latest_bar.close_time,
            )
            return False, latest_bar.close_time, "cursor_initialized_live_mode"

        new_bars = [b for b in feature_bars if b.close_time > last_decision]
        new_bars.sort(key=lambda x: x.close_time)

        if not new_bars:
            return False, latest_bar.close_time, "no_new_feature_bar"

        enabled_users = await self.telegram_users_repo.list_enabled_trading_users()
        if not enabled_users:
            await self.bot_state_repo.set_int(cursor_key, new_bars[-1].close_time)
            log.info(
                "[entry-async] %s %s: no enabled users, cursor advanced to %s",
                symbol,
                tf,
                new_bars[-1].close_time,
            )
            return True, new_bars[-1].close_time, "no_enabled_users"

        all_open_trades = await self.trades_repo.get_open_trades()
        open_trades_by_user: dict[str, list] = defaultdict(list)
        for trade in all_open_trades:
            user_id = getattr(trade, "user_id", None)
            if user_id is not None:
                open_trades_by_user[str(user_id)].append(trade)

        last_processed_close: int | None = None

        for bar in new_bars:
            signal_entry_price = float(bar.c)
            window = [b for b in feature_bars if b.close_time <= bar.close_time]
            result = self.detector.check_long(window)

            log.info(
                "[entry-async] %s %s: now_ms=%s bar_close=%s signal=%s reason=%s rsi=%s min_prev_rsi=%s st=%s close=%.4f",
                symbol,
                tf,
                now_ms,
                bar.close_time,
                result.signal,
                result.reason,
                f"{result.current_rsi:.2f}" if result.current_rsi is not None else "None",
                f"{result.min_prev_rsi:.2f}" if result.min_prev_rsi is not None else "None",
                result.supertrend_bullish,
                signal_entry_price,
            )

            if not result.signal:
                await self.bot_state_repo.set_int(cursor_key, bar.close_time)
                last_processed_close = bar.close_time
                continue

            live_entry_price = await self.exchange.get_mark_price(symbol)
            if live_entry_price is None:
                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{result.rule_id}",
                    event_type="ENTRY_PRICE_UNAVAILABLE",
                    ts=now_ms,
                    symbol=symbol,
                    user_id="system",
                    mode="sandbox",
                    payload={
                        "rule_id": result.rule_id,
                        "tf_entry": tf,
                        "signal_entry_price": signal_entry_price,
                        "entry_bar_close_time": bar.close_time,
                        "features_ver": self.features_ver,
                    },
                )
                await self.bot_state_repo.set_int(cursor_key, bar.close_time)
                last_processed_close = bar.close_time
                log.warning(
                    "[entry-async] %s %s: live price unavailable, signal_price=%.4f entry skipped",
                    symbol,
                    tf,
                    signal_entry_price,
                )
                continue

            deviation_pct = abs((live_entry_price - signal_entry_price) / signal_entry_price) * 100.0

            log.info(
                "[entry-check] %s %s: bar_close=%s signal_price=%.4f live_price=%.4f deviation=%.3f%% max_allowed=%.3f%%",
                symbol,
                tf,
                bar.close_time,
                signal_entry_price,
                live_entry_price,
                deviation_pct,
                self.max_entry_price_deviation_pct,
            )

            if deviation_pct > self.max_entry_price_deviation_pct:
                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{result.rule_id}",
                    event_type="ENTRY_PRICE_MISMATCH",
                    ts=now_ms,
                    symbol=symbol,
                    user_id="system",
                    mode="sandbox",
                    payload={
                        "rule_id": result.rule_id,
                        "tf_entry": tf,
                        "signal_entry_price": signal_entry_price,
                        "live_entry_price": live_entry_price,
                        "deviation_pct": deviation_pct,
                        "max_allowed_deviation_pct": self.max_entry_price_deviation_pct,
                        "entry_bar_close_time": bar.close_time,
                        "features_ver": self.features_ver,
                    },
                )
                await self.bot_state_repo.set_int(cursor_key, bar.close_time)
                last_processed_close = bar.close_time
                log.warning(
                    "[entry-skip] %s %s: price mismatch signal=%.4f live=%.4f deviation=%.3f%% > %.3f%%",
                    symbol,
                    tf,
                    signal_entry_price,
                    live_entry_price,
                    deviation_pct,
                    self.max_entry_price_deviation_pct,
                )
                continue

            for tg_user in enabled_users:
                created = await self._try_open_for_user(
                    tg_user=tg_user,
                    symbol=symbol,
                    tf=tf,
                    now_ms=now_ms,
                    bar=bar,
                    rule_id=result.rule_id,
                    signal_entry_price=signal_entry_price,
                    live_entry_price=live_entry_price,
                    deviation_pct=deviation_pct,
                    open_trades_by_user=open_trades_by_user,
                )

                if created is not None:
                    open_trades_by_user[str(created.user_id)].append(created)

            await self.bot_state_repo.set_int(cursor_key, bar.close_time)
            last_processed_close = bar.close_time

        return True, last_processed_close, "entry_processed"

    async def _try_open_for_user(
        self,
        *,
        tg_user: dict,
        symbol: str,
        tf: str,
        now_ms: int,
        bar,
        rule_id: str,
        signal_entry_price: float,
        live_entry_price: float,
        deviation_pct: float,
        open_trades_by_user: dict[str, list],
    ) -> Trade | None:
        user_id = str(tg_user.get("trading_user_id") or f"tg:{tg_user.get('telegram_id')}")
        trading_mode = str(tg_user.get("trading_mode", "sandbox"))
        user_open_trades = open_trades_by_user.get(user_id, [])

        if self.cooldowns_repo is not None:
            cooldown = await self.cooldowns_repo.get_active_cooldown(
                runtime_user_id=user_id,
                symbol=symbol,
                reason="loss_stop",
            )
            if cooldown is not None:
                cooldown_until = cooldown.get("cooldown_until")

                await self.trade_events_repo.add_event(
                    trade_id=f"debug:global:{symbol}:{tf}:{bar.close_time}:{rule_id}:{user_id}",
                    event_type="ENTRY_BLOCKED_COOLDOWN",
                    ts=now_ms,
                    symbol=symbol,
                    user_id=user_id,
                    mode=trading_mode,
                    payload={
                        "reason": "loss_stop_cooldown_active",
                        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                        "tf_entry": tf,
                        "rule_id": rule_id,
                        "features_ver": self.features_ver,
                    },
                )

                log.info(
                    "[entry-async] %s %s user=%s: blocked by cooldown until=%s",
                    symbol,
                    tf,
                    user_id,
                    cooldown_until,
                )
                return None

        policy_decision = self.entry_policy.can_open_trade(
            user_id=user_id,
            symbol=symbol,
            tf=tf,
            rule_id=rule_id,
            open_trades=user_open_trades,
        )

        if not policy_decision.allowed:
            log.info(
                "[entry-async] %s %s user=%s: policy blocked rule_id=%s reason=%s conflict_trade_id=%s",
                symbol,
                tf,
                user_id,
                rule_id,
                policy_decision.reason,
                policy_decision.conflict_trade_id,
            )
            return None

        history = await self.trades_repo.get_trades(
            user_id=user_id,
            mode=trading_mode,
            limit=5000,
        )

        sizing = self._build_user_sizing(
            tg_user=tg_user,
            entry_price=live_entry_price,
            history=history,
        )

        entry_price = live_entry_price
        sl_price = entry_price * 0.98

        trade = Trade(
            trade_id=f"{user_id}:{symbol}:{tf}:{bar.close_time}:{rule_id}",
            user_id=user_id,
            symbol=symbol,
            side=Side.LONG,
            status=TradeStatus.OPEN,
            opened_at=now_ms,
            entry_price=entry_price,
            qty=sizing["qty"],
            leverage=int(sizing["leverage"]),
            stake_usd=sizing["stake_usd"],
            tf_entry=tf,
            model_id=rule_id,
            entry_bar_close_time=bar.close_time,
            sl_price=sl_price,
            mode=trading_mode,
            tp_hit_count=0,
            remaining_pct=1.0,
            exit_last_check_at=now_ms,
            qty_remaining=sizing["qty"],
            realized_pnl_usd=0.0,
        )

        trade = self.broker.open_position(trade)
        await self.trades_repo.create_trade(trade)

        await self.trade_events_repo.add_event(
            trade_id=trade.trade_id,
            event_type="OPENED",
            ts=now_ms,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload={
                "telegram_id": tg_user.get("telegram_id"),
                "entry_price": trade.entry_price,
                "signal_entry_price": signal_entry_price,
                "live_entry_price": live_entry_price,
                "entry_price_deviation_pct": deviation_pct,
                "qty": trade.qty,
                "stake_usd": trade.stake_usd,
                "sl_price": trade.sl_price,
                "tf_entry": trade.tf_entry,
                "model_id": trade.model_id,
                "rule_id": rule_id,
                "features_ver": self.features_ver,
                "entry_bar_close_time": trade.entry_bar_close_time,
                "rsi14": (bar.features or {}).get("rsi14"),
                "supertrend_bullish_10_3_0": (bar.features or {}).get("supertrend_bullish_10_3_0"),
                "default_leverage": tg_user.get("default_leverage", 5),
                "plan_code": tg_user.get("plan_code"),
            },
        )

        log.info(
            "[entry-async] %s %s: CREATED trade_id=%s user=%s mode=%s signal_entry=%.4f live_entry=%.4f stake=%.4f leverage=x%s qty=%s plan=%s",
            symbol,
            tf,
            trade.trade_id,
            user_id,
            trading_mode,
            signal_entry_price,
            live_entry_price,
            trade.stake_usd,
            trade.leverage,
            trade.qty,
            tg_user.get("plan_code"),
        )

        return trade

    def _build_user_sizing(
        self,
        *,
        tg_user: dict,
        entry_price: float,
        history: list,
    ) -> dict[str, float]:
        default_stake_mode = str(tg_user.get("default_stake_mode", "percent"))
        default_stake_value = float(tg_user.get("default_stake_value", 1.0) or 1.0)
        default_leverage = int(tg_user.get("default_leverage", 5) or 5)

        balance = self._estimate_balance(history)

        if default_stake_mode == "percent":
            stake_usd = balance * (default_stake_value / 100.0)
        else:
            stake_usd = default_stake_value

        if stake_usd <= 0:
            stake_usd = 1.0

        qty = (stake_usd * default_leverage) / entry_price if entry_price > 0 else 0.0

        return {
            "stake_usd": float(stake_usd),
            "leverage": float(default_leverage),
            "qty": float(qty),
        }

    def _estimate_balance(self, history: list) -> float:
        realized = 0.0
        for trade in history:
            pnl = getattr(trade, "realized_pnl_usd", 0.0)
            realized += float(pnl or 0.0)
        return self.sandbox_start_balance_usd + realized