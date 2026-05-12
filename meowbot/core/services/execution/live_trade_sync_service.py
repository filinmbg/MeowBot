from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from meowbot.core.domain.enums import Side, TradeStatus
from meowbot.core.domain.types import Trade
from meowbot.core.services.execution.exit.exit_profile import (
    ExitProfile,
    exit_profile_to_dict,
    get_exit_profile,
)
from meowbot.core.services.runtime.active_trades_cache import ActiveTradesCache
from meowbot.core.services.runtime.symbol_validator import validate_binance_usdt_perp_symbol


log = logging.getLogger("meowbot")

FINAL_TRADE_STATUSES = {TradeStatus.CLOSED, TradeStatus.ERROR_INVALID_SYMBOL}
INCOME_SYNC_MAX_CONCURRENT = 2
INCOME_SYNC_BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0)


class LiveTradeSyncService:
    def __init__(
        self,
        *,
        trades_repo,
        trade_events_repo,
        account_provider,
        live_broker,
        active_trades_cache=None,
        live_poll_interval_ms: int = 15_000,
        pnl_retry_delays_seconds: tuple[float, ...] | None = None,
    ) -> None:
        self.trades_repo = trades_repo
        self.trade_events_repo = trade_events_repo
        self.account_provider = account_provider
        self.live_broker = live_broker
        self.active_trades_cache = active_trades_cache or ActiveTradesCache()
        self.live_poll_interval_ms = int(live_poll_interval_ms)
        self.pnl_retry_delays_seconds = (
            tuple(float(delay) for delay in pnl_retry_delays_seconds)
            if pnl_retry_delays_seconds is not None
            else (2.0, 5.0, 10.0)
        )
        self._income_sync_gate = asyncio.Semaphore(INCOME_SYNC_MAX_CONCURRENT)
        self._income_sync_locks: dict[str, asyncio.Lock] = {}
        self._income_result_cache: dict[str, dict[str, Any]] = {}

    async def sync_user_open_trades(
        self,
        *,
        runtime_user_id: str,
        now_ms: int | None = None,
        reason: str = "poll",
        force: bool = False,
    ) -> list[Trade]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        trades = self.active_trades_cache.get_user_open_trades(runtime_user_id, mode="live")
        updated: list[Trade] = []
        for trade in trades:
            current = await self.sync_trade(
                trade,
                now_ms=now_ms,
                reason=reason,
                force=force,
            )
            updated.append(current)
            self._update_cache(current)
            await self._persist_trade(current)
        return updated

    async def handle_user_stream_event(
        self,
        *,
        runtime_user_id: str,
        payload: dict[str, Any],
        now_ms: int | None = None,
    ) -> list[Trade]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        event_symbol = self._extract_stream_symbol(payload)
        trades = self.active_trades_cache.get_user_open_trades(runtime_user_id, mode="live")
        if event_symbol:
            trades = [trade for trade in trades if str(trade.symbol).upper() == event_symbol]

        updated: list[Trade] = []
        for trade in trades:
            current = await self.sync_trade(
                trade,
                now_ms=now_ms,
                reason="user_stream",
                force=True,
            )
            updated.append(current)
            self._update_cache(current)
            await self._persist_trade(current)
        return updated

    def _update_cache(self, trade: Trade) -> None:
        self.active_trades_cache.update_from_trade(trade)

    async def _persist_trade(self, trade: Trade) -> None:
        try:
            await self.trades_repo.update_trade(trade)
        except Exception as exc:
            log.warning(
                "[live-sync] trade persistence skipped trade_id=%s symbol=%s error=%s:%s",
                getattr(trade, "trade_id", "-"),
                getattr(trade, "symbol", "-"),
                type(exc).__name__,
                exc,
            )

    async def sync_trade(
        self,
        trade: Trade,
        *,
        now_ms: int,
        reason: str,
        force: bool = False,
    ) -> Trade:
        if str(trade.mode) != "live":
            return trade
        if self._can_skip_finalized_trade(trade):
            return trade

        symbol_validation = validate_binance_usdt_perp_symbol(getattr(trade, "symbol", ""))
        if not symbol_validation.valid:
            return await self._mark_invalid_symbol_trade(
                trade,
                now_ms=now_ms,
                reason=reason,
                invalid_reason=symbol_validation.reason,
            )
        if (
            str(getattr(trade, "pnl_status", "") or "") == "pending"
            and bool(getattr(trade, "exchange_position_confirmed_flat", False))
            and bool(getattr(trade, "cleanup_completed", False))
            and self._income_sync_cooldown_active(trade, now_ms=now_ms)
        ):
            return trade

        last_sync_at = int(getattr(trade, "exchange_last_sync_at", 0) or 0)
        if (
            not force
            and reason == "poll"
            and last_sync_at > 0
            and (now_ms - last_sync_at) < self.live_poll_interval_ms
        ):
            return trade

        client, key_row, error = await self.account_provider.build_client(
            runtime_user_id=str(trade.user_id),
            require_active=False,
        )
        if client is None:
            return await self._mark_sync_problem(
                trade,
                now_ms=now_ms,
                reason=reason,
                error=error or "live_api_unavailable",
                emit_invalid_api=True,
            )

        try:
            if str((key_row or {}).get("validation_status") or "") != "valid":
                await self._emit_invalid_api_once(trade, now_ms=now_ms, reason="validation_status_invalid")

            open_orders = await client.get_open_orders(symbol=trade.symbol, raise_on_error=True)
            all_orders = await client.get_all_orders(symbol=trade.symbol, limit=50, raise_on_error=True)
            open_algo_orders = await self._call_critical_list(
                client,
                "get_open_algo_orders",
                symbol=trade.symbol,
                raise_on_error=True,
            )
            all_algo_orders = await self._call_critical_list(
                client,
                "get_all_algo_orders",
                symbol=trade.symbol,
                limit=50,
                raise_on_error=True,
            )
            position = await client.get_position_risk(symbol=trade.symbol, raise_on_error=True)
            if position is None:
                raise RuntimeError("position_risk_unavailable")
        except Exception as exc:
            exchange_error_details = self._exchange_error_details(exc)
            await client.close()
            return await self._mark_sync_problem(
                trade,
                now_ms=now_ms,
                reason=reason,
                error=f"{type(exc).__name__}: {exc}",
                emit_invalid_api=False,
                error_details=exchange_error_details,
            )

        try:
            position_match = self._match_exchange_position(trade, position)
            current = replace(
                trade,
                exchange_last_sync_at=now_ms,
                exchange_last_sync_reason=reason,
                exchange_sync_status="ok",
                exchange_sync_error=None,
                exchange_position_amt=position_match["position_qty"],
                exchange_tp_orders=self._refresh_tp_orders(
                    trade,
                    [*all_orders, *all_algo_orders],
                    [*open_orders, *open_algo_orders],
                ),
                exchange_stop_order=self._refresh_stop_order(
                    trade,
                    [*all_orders, *all_algo_orders],
                    [*open_orders, *open_algo_orders],
                ),
                exchange_order_ids=self._refresh_order_ids(
                    trade,
                    [*all_orders, *all_algo_orders],
                    [*open_orders, *open_algo_orders],
                ),
            )

            final_tp_stage = self._final_tp_stage(current)
            for stage in self._tp_stages(current):
                if current.tp_hit_count >= stage:
                    continue
                order = self._find_tp_order(current, stage)
                if not order:
                    continue
                if str(order.get("status") or "") != "FILLED":
                    continue

                fill_qty = self._to_float(order.get("executedQty") or order.get("origQty"))
                fill_price = self._order_fill_price(order, fallback=current.entry_price)
                tp_count = self._tp_count(current)
                close_fraction = self._tp_order_close_fraction(current, order, fill_qty)
                post_fill_position_match = await self._refresh_position_match(client=client, trade=current)
                if self._position_fetch_failed(post_fill_position_match):
                    return await self._mark_sync_problem(
                        trade,
                        now_ms=now_ms,
                        reason=reason,
                        error=f"critical_exchange_state_unavailable:{post_fill_position_match.get('reason')}",
                        emit_invalid_api=False,
                        error_details=post_fill_position_match.get("error_details"),
                    )
                position_match = post_fill_position_match
                exchange_flat_after_fill = bool(post_fill_position_match.get("confident_flat"))
                exchange_position_qty = None if post_fill_position_match.get("ambiguous") else float(post_fill_position_match.get("position_qty") or 0.0)
                local_qty_remaining_before = float(current.qty_remaining or 0.0)
                local_qty_remaining_after_without_exchange = self._local_remaining_after_fill(current, fill_qty)
                if stage < final_tp_stage and not exchange_flat_after_fill:
                    current = self._apply_tp_fill(
                        current,
                        stage=stage,
                        fill_qty=fill_qty,
                        fill_price=fill_price,
                        exchange_position_qty=exchange_position_qty,
                    )
                    await self._emit_event_once(
                        trade=current,
                        event_type="TP_HIT",
                        idempotency_key=f"trade:{current.trade_id}:tp:{stage}",
                        ts=now_ms,
                        payload={
                            "side": getattr(current.side, "value", str(current.side)),
                            "tp_index": stage,
                            "tp_count": tp_count,
                            "tp_close_fraction": close_fraction,
                            "tp_close_pct": close_fraction * 100.0,
                            "tp_price": fill_price,
                            "qty_closed": fill_qty,
                            "qty": current.qty,
                            "qty_remaining": current.qty_remaining,
                            "remaining_pct": current.remaining_pct,
                            "exchange_position_amt_after_close": current.exchange_position_amt,
                            "exchange_remaining_pct": current.remaining_pct,
                            "realized_pnl_usd": current.realized_pnl_usd,
                            "new_sl_price": current.sl_price,
                            "trade_closed": False,
                        },
                    )
                    if (
                        exchange_position_qty is not None
                        and exchange_position_qty > 1e-12
                        and local_qty_remaining_after_without_exchange <= 1e-12
                    ):
                        await self._emit_partial_close_detected(
                            current,
                            source=f"TP{stage}_HIT",
                            stage=stage,
                            now_ms=now_ms,
                            planned_close_qty=self._to_float(order.get("origQty") or order.get("quantity")),
                            actual_executed_qty=fill_qty,
                            exchange_position_qty=exchange_position_qty,
                            position_match=post_fill_position_match,
                            local_qty_remaining_before=local_qty_remaining_before,
                            local_qty_remaining_after_without_exchange=local_qty_remaining_after_without_exchange,
                        )
                    current = await self._repair_remaining_position_protection(
                        current,
                        client=client,
                        new_stop_price=current.sl_price,
                        now_ms=now_ms,
                        reason=f"TP{stage}_HIT",
                        current_open_orders=[*open_orders, *open_algo_orders],
                        position_match=position_match,
                        all_orders=[*all_orders, *all_algo_orders],
                    )
                    open_orders = self._replace_open_stop_snapshot(open_orders, current.exchange_stop_order)
                    open_algo_orders = self._replace_open_stop_snapshot(open_algo_orders, current.exchange_stop_order)
                elif exchange_flat_after_fill:
                    current = self._apply_full_close(
                        current,
                        close_price=fill_price,
                        reason=f"TP{stage}_HIT",
                        now_ms=now_ms,
                    )
                    current, pnl_verified = await self._finalize_closed_trade_with_exchange_pnl(
                        client=client,
                        trade=current,
                        now_ms=now_ms,
                        close_reason=f"TP{stage}_HIT",
                        close_orders=[*all_orders, *all_algo_orders],
                    )
                    if pnl_verified:
                        await self._emit_event_once(
                            trade=current,
                            event_type="CLOSED",
                            idempotency_key=f"trade:{current.trade_id}:closed:tp{stage}",
                            ts=now_ms,
                            payload={
                                "side": getattr(current.side, "value", str(current.side)),
                                "reason": f"TP{stage}_HIT",
                                "tp_index": stage,
                                "tp_count": tp_count,
                                "tp_close_fraction": close_fraction,
                                "tp_close_pct": close_fraction * 100.0,
                                "close_price": current.close_price,
                                "qty_closed": fill_qty,
                                "qty_remaining": current.qty_remaining,
                                "remaining_pct": current.remaining_pct,
                                "realized_pnl_usd": current.realized_pnl_usd,
                                "tp_hit_count": current.tp_hit_count,
                                "trade_closed": True,
                                **self._pnl_payload(current),
                            },
                        )
                    current = await self._cleanup_stale_exit_orders_if_confirmed_flat(
                        client=client,
                        trade=current,
                        now_ms=now_ms,
                        reason=f"post_close_cleanup:tp{stage}",
                    )
                else:
                    current = self._apply_tp_fill(
                        current,
                        stage=stage,
                        fill_qty=fill_qty,
                        fill_price=fill_price,
                        exchange_position_qty=exchange_position_qty,
                    )
                    await self._emit_event_once(
                        trade=current,
                        event_type="TP_HIT",
                        idempotency_key=f"trade:{current.trade_id}:tp:{stage}",
                        ts=now_ms,
                        payload={
                            "side": getattr(current.side, "value", str(current.side)),
                            "tp_index": stage,
                            "tp_count": tp_count,
                            "tp_close_fraction": close_fraction,
                            "tp_close_pct": close_fraction * 100.0,
                            "tp_price": fill_price,
                            "qty_closed": fill_qty,
                            "qty": current.qty,
                            "qty_remaining": current.qty_remaining,
                            "remaining_pct": current.remaining_pct,
                            "exchange_position_amt_after_close": current.exchange_position_amt,
                            "exchange_remaining_pct": current.remaining_pct,
                            "realized_pnl_usd": current.realized_pnl_usd,
                            "new_sl_price": current.sl_price,
                            "trade_closed": False,
                        },
                    )
                    await self._emit_partial_close_detected(
                        current,
                        source=f"TP{stage}_HIT",
                        stage=stage,
                        now_ms=now_ms,
                        planned_close_qty=self._to_float(order.get("origQty") or order.get("quantity")),
                        actual_executed_qty=fill_qty,
                        exchange_position_qty=exchange_position_qty,
                        position_match=post_fill_position_match,
                        local_qty_remaining_before=local_qty_remaining_before,
                        local_qty_remaining_after_without_exchange=local_qty_remaining_after_without_exchange,
                    )
                    current = await self._repair_remaining_position_protection(
                        current,
                        client=client,
                        new_stop_price=current.sl_price,
                        now_ms=now_ms,
                        reason=f"TP{stage}_HIT",
                        current_open_orders=[*open_orders, *open_algo_orders],
                        position_match=position_match,
                        all_orders=[*all_orders, *all_algo_orders],
                    )
                    open_orders = self._replace_open_stop_snapshot(open_orders, current.exchange_stop_order)
                    open_algo_orders = self._replace_open_stop_snapshot(open_algo_orders, current.exchange_stop_order)

            if self._is_open(current):
                stop_order = current.exchange_stop_order or {}
                if str(stop_order.get("status") or "") == "FILLED":
                    stop_price = self._order_fill_price(stop_order, fallback=current.sl_price)
                    stop_fill_qty = self._to_float(stop_order.get("executedQty") or stop_order.get("origQty") or stop_order.get("quantity"))
                    post_stop_position_match = await self._refresh_position_match(client=client, trade=current)
                    if self._position_fetch_failed(post_stop_position_match):
                        return await self._mark_sync_problem(
                            trade,
                            now_ms=now_ms,
                            reason=reason,
                            error=f"critical_exchange_state_unavailable:{post_stop_position_match.get('reason')}",
                            emit_invalid_api=False,
                            error_details=post_stop_position_match.get("error_details"),
                        )
                    position_match = post_stop_position_match
                    if post_stop_position_match.get("confident_flat"):
                        current = self._apply_full_close(
                            current,
                            close_price=stop_price,
                            reason="STOP_LOSS_HIT",
                            now_ms=now_ms,
                        )
                        current, pnl_verified = await self._finalize_closed_trade_with_exchange_pnl(
                            client=client,
                            trade=current,
                            now_ms=now_ms,
                            close_reason="STOP_LOSS_HIT",
                            close_orders=[*all_orders, *all_algo_orders],
                        )
                        if pnl_verified:
                            await self._emit_event_once(
                                trade=current,
                                event_type="STOP",
                                idempotency_key=f"trade:{current.trade_id}:stop",
                                ts=now_ms,
                                payload={
                                    "side": getattr(current.side, "value", str(current.side)),
                                    "reason": "STOP_LOSS_HIT",
                                    "close_price": current.close_price,
                                    "realized_pnl_usd": current.realized_pnl_usd,
                                    "tp_hit_count": current.tp_hit_count,
                                    **self._pnl_payload(current),
                                },
                            )
                        current = await self._cleanup_stale_exit_orders_if_confirmed_flat(
                            client=client,
                            trade=current,
                            now_ms=now_ms,
                            reason="post_close_cleanup:stop_loss",
                        )
                    else:
                        exchange_position_qty = None if post_stop_position_match.get("ambiguous") else float(post_stop_position_match.get("position_qty") or 0.0)
                        local_qty_remaining_before = float(current.qty_remaining or 0.0)
                        local_qty_remaining_after_without_exchange = self._local_remaining_after_fill(current, stop_fill_qty)
                        current = self._apply_partial_close_fill(
                            current,
                            fill_qty=stop_fill_qty,
                            fill_price=stop_price,
                            exchange_position_qty=exchange_position_qty,
                        )
                        await self._emit_partial_close_detected(
                            current,
                            source="STOP_LOSS_HIT",
                            stage=None,
                            now_ms=now_ms,
                            planned_close_qty=self._to_float(stop_order.get("origQty") or stop_order.get("quantity")),
                            actual_executed_qty=stop_fill_qty,
                            exchange_position_qty=exchange_position_qty,
                            position_match=post_stop_position_match,
                            local_qty_remaining_before=local_qty_remaining_before,
                            local_qty_remaining_after_without_exchange=local_qty_remaining_after_without_exchange,
                        )
                        current = await self._repair_remaining_position_protection(
                            current,
                            client=client,
                            new_stop_price=current.sl_price,
                            now_ms=now_ms,
                            reason="STOP_LOSS_HIT",
                            current_open_orders=[*open_orders, *open_algo_orders],
                            position_match=position_match,
                            all_orders=[*all_orders, *all_algo_orders],
                        )
                        open_orders = self._replace_open_stop_snapshot(open_orders, current.exchange_stop_order)
                        open_algo_orders = self._replace_open_stop_snapshot(open_algo_orders, current.exchange_stop_order)

            if self._is_open(current):
                current, position_match, soft_stop_terminal = await self._process_v2_soft_stop(
                    client=client,
                    trade=current,
                    position_match=position_match,
                    now_ms=now_ms,
                    all_orders=[*all_orders, *all_algo_orders],
                )
                if soft_stop_terminal:
                    return current

            if self._is_open(current):
                if position_match["ambiguous"]:
                    current = replace(
                        current,
                        exchange_sync_status="position_match_ambiguous",
                        exchange_sync_error=str(position_match["reason"]),
                    )
                    await self._emit_admin_debug_once(
                        current,
                        event_type="LIVE_SYNC_MISMATCH",
                        idempotency_key=f"trade:{current.trade_id}:mismatch:position_ambiguous:{position_match['reason']}",
                        ts=now_ms,
                        payload={
                            "reason": "position_match_ambiguous",
                            "ambiguity_reason": position_match["reason"],
                            **self._position_diagnostics(current, position_match),
                        },
                    )
                    return current

                position_qty = float(position_match["position_qty"] or 0.0)
                if position_match["confident_flat"]:
                    close_price, close_price_source = self._external_flat_close_price(
                        current,
                        [*all_orders, *all_algo_orders],
                    )
                    current = self._apply_full_close(
                        current,
                        close_price=close_price,
                        reason="EXCHANGE_POSITION_FLAT",
                        now_ms=now_ms,
                    )
                    current, pnl_verified = await self._finalize_closed_trade_with_exchange_pnl(
                        client=client,
                        trade=current,
                        now_ms=now_ms,
                        close_reason="EXCHANGE_POSITION_FLAT",
                        close_orders=[*all_orders, *all_algo_orders],
                    )
                    if pnl_verified:
                        await self._emit_event_once(
                            trade=current,
                            event_type="CLOSED",
                            idempotency_key=f"trade:{current.trade_id}:closed:flat",
                            ts=now_ms,
                            payload={
                                "side": getattr(current.side, "value", str(current.side)),
                                "reason": "EXCHANGE_POSITION_FLAT",
                                "close_price": current.close_price,
                                "close_price_source": close_price_source,
                                "realized_pnl_usd": current.realized_pnl_usd,
                                "tp_hit_count": current.tp_hit_count,
                                **self._pnl_payload(current),
                            },
                        )
                    await self._emit_admin_debug_once(
                        current,
                        event_type="LIVE_SYNC_RECONCILED",
                        idempotency_key=f"trade:{current.trade_id}:reconciled:flat",
                        ts=now_ms,
                        payload={
                            "reason": "exchange_position_flat",
                            "detected_close_source": "exchange",
                            "close_price_source": close_price_source,
                            **self._position_diagnostics(current, position_match),
                        },
                    )
                    current = await self._cleanup_stale_exit_orders_if_confirmed_flat(
                        client=client,
                        trade=current,
                        now_ms=now_ms,
                        reason="post_close_cleanup:exchange_position_flat",
                        position_match=position_match,
                    )
                else:
                    local_qty_before_reconcile = float(current.qty_remaining or 0.0)
                    qty_gap = abs(float(current.qty_remaining or 0.0) - position_qty)
                    if qty_gap > max(position_qty * 0.01, 1e-8):
                        current = replace(
                            current,
                            qty_remaining=position_qty,
                            remaining_pct=(position_qty / current.qty) if current.qty > 0 else 0.0,
                            exchange_position_amt=position_qty,
                            exchange_sync_status="qty_reconciled",
                        )
                        log.info(
                            "[live-sync] reconciled qty from exchange trade_id=%s symbol=%s local_qty_remaining=%s exchange_qty_remaining=%s",
                            current.trade_id,
                            current.symbol,
                            local_qty_before_reconcile,
                            position_qty,
                        )
                        await self._emit_admin_debug_once(
                            current,
                            event_type="LIVE_SYNC_RECONCILED",
                            idempotency_key=f"trade:{current.trade_id}:reconciled:qty:{position_qty:.8f}",
                            ts=now_ms,
                            payload={
                                "reason": "qty_reconciled_from_exchange",
                                "local_qty_remaining": local_qty_before_reconcile,
                                "exchange_qty_remaining": position_qty,
                                **self._position_diagnostics(current, position_match),
                            },
                        )
                        current = await self._repair_remaining_position_protection(
                            current,
                            client=client,
                            new_stop_price=current.sl_price,
                            now_ms=now_ms,
                            reason="qty_reconciled_from_exchange",
                            current_open_orders=[*open_orders, *open_algo_orders],
                            position_match=position_match,
                            all_orders=[*all_orders, *all_algo_orders],
                        )
                        open_orders = self._replace_open_stop_snapshot(open_orders, current.exchange_stop_order)
                        open_algo_orders = self._replace_open_stop_snapshot(open_algo_orders, current.exchange_stop_order)

                    if self._is_open(current) and not self._has_open_stop_order(current, [*open_orders, *open_algo_orders]):
                        current = await self._repair_remaining_position_protection(
                            current,
                            client=client,
                            new_stop_price=current.sl_price,
                            now_ms=now_ms,
                            reason="missing_stop_order",
                            current_open_orders=[*open_orders, *open_algo_orders],
                            position_match=position_match,
                            all_orders=[*all_orders, *all_algo_orders],
                        )

            return current
        finally:
            await client.close()

    async def _process_v2_soft_stop(
        self,
        *,
        client: Any,
        trade: Trade,
        position_match: dict[str, Any],
        now_ms: int,
        all_orders: list[dict[str, Any]],
    ) -> tuple[Trade, dict[str, Any], bool]:
        profile = get_exit_profile(
            getattr(trade, "strategy_version", "v1"),
            signal_level=getattr(trade, "signal_level", None),
        )
        if not profile.soft_stop_enabled:
            return trade, position_match, False
        if position_match.get("ambiguous") or position_match.get("confident_flat"):
            return trade, position_match, False

        current_price = self._current_price_from_position_match(position_match)
        if current_price <= 0:
            return trade, position_match, False

        current = self._ensure_soft_stop_profile_fields(trade, profile)
        profit_pct = self._profit_pct(current, current_price)
        activated_now = False
        raised_now = False

        tp1_hit = int(getattr(current, "tp_hit_count", 0) or 0) >= 1
        if getattr(current, "soft_stop_activated_at", None) is None and tp1_hit:
            current = self._activate_soft_stop(current, profile=profile, now_ms=now_ms)
            activated_now = True
            soft_stop_price = self._soft_stop_price(current)
            current = replace(current, soft_stop_trigger_price=soft_stop_price)
            log.info(
                "[soft-stop-activated] trade_id=%s symbol=%s signal_level=%s activation_reason=tp1_hit tp_index=1 soft_stop_pct=%s soft_stop_price=%.8f",
                current.trade_id,
                current.symbol,
                getattr(current, "signal_level", None),
                current.soft_stop_current_pct,
                soft_stop_price,
            )
            await self._emit_event_once(
                trade=current,
                event_type="SOFT_STOP_ACTIVATED",
                idempotency_key=f"trade:{current.trade_id}:soft_stop:activated",
                ts=now_ms,
                payload={
                    **self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=profit_pct,
                        close_reason=None,
                    ),
                    "activation_reason": "tp1_hit",
                    "tp_index": 1,
                },
            )

        if getattr(current, "soft_stop_activated_at", None) is not None:
            raised = self._raise_soft_stop_if_due(current, profile=profile, now_ms=now_ms)
            if raised is not current:
                current = raised
                raised_now = True
                await self._emit_event_once(
                    trade=current,
                    event_type="SOFT_STOP_RAISED",
                    idempotency_key=(
                        f"trade:{current.trade_id}:soft_stop:raised:"
                        f"{int(current.soft_stop_last_raise_at or now_ms)}:"
                        f"{float(current.soft_stop_current_pct or 0.0):.4f}"
                    ),
                    ts=now_ms,
                    payload=self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=profit_pct,
                        close_reason=None,
                    ),
                )

            trigger_price = self._soft_stop_price(current)
            current = replace(current, soft_stop_trigger_price=trigger_price)
            should_close = (
                current.side == Side.LONG and current_price <= trigger_price
            ) or (
                current.side == Side.SHORT and current_price >= trigger_price
            )
            log.info(
                "[live-sync] soft stop state trade_id=%s symbol=%s strategy_version=%s signal_level=%s profit_pct=%.4f activation_pct=%.4f increment_pct=%.4f interval_seconds=%s soft_stop_pct=%s soft_stop_price=%.8f current_price=%.8f should_close=%s activated_now=%s raised_now=%s",
                current.trade_id,
                current.symbol,
                current.strategy_version,
                getattr(current, "signal_level", None),
                profit_pct,
                profile.soft_stop_activation_pct,
                getattr(profile, "soft_stop_increment_pct", profile.soft_stop_hourly_increment_pct),
                profile.soft_stop_increment_interval_seconds,
                current.soft_stop_current_pct,
                trigger_price,
                current_price,
                should_close,
                activated_now,
                raised_now,
            )
            if should_close:
                return await self._close_by_soft_stop(
                    client=client,
                    trade=current,
                    position_match=position_match,
                    profile=profile,
                    now_ms=now_ms,
                    current_price=current_price,
                    current_profit_pct=profit_pct,
                    all_orders=all_orders,
                )

        return current, position_match, False

    def _ensure_soft_stop_profile_fields(self, trade: Trade, profile: ExitProfile) -> Trade:
        return replace(
            trade,
            exit_profile=exit_profile_to_dict(profile),
            soft_stop_enabled=True,
        )

    def _activate_soft_stop(self, trade: Trade, *, profile: ExitProfile, now_ms: int) -> Trade:
        interval_ms = int(profile.soft_stop_increment_interval_seconds) * 1000
        return replace(
            trade,
            soft_stop_enabled=True,
            soft_stop_activated_at=now_ms,
            soft_stop_current_pct=float(profile.soft_stop_start_pct),
            soft_stop_last_raise_at=now_ms,
            soft_stop_next_raise_at=now_ms + interval_ms,
        )

    def _raise_soft_stop_if_due(self, trade: Trade, *, profile: ExitProfile, now_ms: int) -> Trade:
        interval_ms = max(int(profile.soft_stop_increment_interval_seconds) * 1000, 1)
        next_raise_at = int(
            getattr(trade, "soft_stop_next_raise_at", None)
            or (int(getattr(trade, "soft_stop_activated_at", 0) or now_ms) + interval_ms)
        )
        if now_ms < next_raise_at:
            return trade

        increments = int((now_ms - next_raise_at) // interval_ms) + 1
        current_pct = float(getattr(trade, "soft_stop_current_pct", None) or profile.soft_stop_start_pct)
        increment_pct = float(getattr(profile, "soft_stop_increment_pct", profile.soft_stop_hourly_increment_pct))
        new_pct = current_pct + (increment_pct * increments)
        last_raise_at = next_raise_at + ((increments - 1) * interval_ms)
        return replace(
            trade,
            soft_stop_current_pct=new_pct,
            soft_stop_last_raise_at=last_raise_at,
            soft_stop_next_raise_at=next_raise_at + (increments * interval_ms),
        )

    async def _close_by_soft_stop(
        self,
        *,
        client: Any,
        trade: Trade,
        position_match: dict[str, Any],
        profile: ExitProfile,
        now_ms: int,
        current_price: float,
        current_profit_pct: float,
        all_orders: list[dict[str, Any]],
    ) -> tuple[Trade, dict[str, Any], bool]:
        await self._emit_admin_debug_once(
            trade,
            event_type="LIVE_SOFT_STOP_TRIGGERED",
            idempotency_key=f"trade:{trade.trade_id}:soft_stop:triggered:{float(trade.soft_stop_current_pct or 0.0):.4f}",
            ts=now_ms,
            payload=self._soft_stop_payload(
                trade,
                profile=profile,
                current_price=current_price,
                current_profit_pct=current_profit_pct,
                close_reason="SOFT_TRAILING_STOP",
            ),
        )

        closed_candidate = await self.live_broker.close_position_market(
            trade,
            now_ms=now_ms,
            reason="SOFT_TRAILING_STOP",
        )
        post_close_position_match = await self._refresh_position_match(client=client, trade=closed_candidate)
        if self._position_fetch_failed(post_close_position_match):
            current = await self._mark_sync_problem(
                trade,
                now_ms=now_ms,
                reason="soft_trailing_stop",
                error=f"critical_exchange_state_unavailable:{post_close_position_match.get('reason')}",
                emit_invalid_api=False,
                error_details=post_close_position_match.get("error_details"),
            )
            return current, post_close_position_match, True

        if not post_close_position_match.get("confident_flat"):
            exchange_position_qty = None if post_close_position_match.get("ambiguous") else float(post_close_position_match.get("position_qty") or 0.0)
            current = replace(
                closed_candidate,
                status=TradeStatus.OPEN,
                qty_remaining=exchange_position_qty if exchange_position_qty is not None else float(trade.qty_remaining or 0.0),
                remaining_pct=((exchange_position_qty or 0.0) / trade.qty) if trade.qty > 0 and exchange_position_qty is not None else trade.remaining_pct,
                exchange_position_amt=exchange_position_qty,
                exchange_sync_status="soft_stop_close_not_flat",
                exchange_sync_error="exchange_position_still_open_after_soft_stop_market_close",
            )
            await self._emit_admin_debug_once(
                current,
                event_type="LIVE_CLOSE_MISMATCH_POSITION_STILL_OPEN",
                idempotency_key=f"trade:{current.trade_id}:soft_stop_close_position_still_open:{float(exchange_position_qty or 0.0):.8f}",
                ts=now_ms,
                payload={
                    "reason": "soft_trailing_stop_position_still_open",
                    "close_reason": "SOFT_TRAILING_STOP",
                    "exchange_position_amt_after_close": exchange_position_qty,
                    "decision": "open",
                    **self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=current_profit_pct,
                        close_reason="SOFT_TRAILING_STOP",
                    ),
                    **self._position_diagnostics(current, post_close_position_match),
                },
            )
            return current, post_close_position_match, True

        current = self._apply_full_close(
            closed_candidate,
            close_price=current_price,
            reason="SOFT_TRAILING_STOP",
            now_ms=now_ms,
        )
        current, pnl_verified = await self._finalize_closed_trade_with_exchange_pnl(
            client=client,
            trade=current,
            now_ms=now_ms,
            close_reason="SOFT_TRAILING_STOP",
            close_orders=all_orders,
        )
        if pnl_verified:
            await self._emit_event_once(
                trade=current,
                event_type="CLOSED",
                idempotency_key=f"trade:{current.trade_id}:closed:soft_trailing_stop",
                ts=now_ms,
                payload={
                    "side": getattr(current.side, "value", str(current.side)),
                    "reason": "SOFT_TRAILING_STOP",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                    **self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=current_profit_pct,
                        close_reason="SOFT_TRAILING_STOP",
                    ),
                    **self._pnl_payload(current),
                },
            )
        current = await self._cleanup_stale_exit_orders_if_confirmed_flat(
            client=client,
            trade=current,
            now_ms=now_ms,
            reason="post_close_cleanup:soft_trailing_stop",
            position_match=post_close_position_match,
        )
        return current, post_close_position_match, True

    async def _close_by_immediate_stop(
        self,
        *,
        client: Any,
        trade: Trade,
        position_match: dict[str, Any],
        now_ms: int,
        current_price: float,
        stop_price: float,
        replacement_reason: str,
        all_orders: list[dict[str, Any]] | None,
    ) -> tuple[Trade, dict[str, Any], bool]:
        profile = get_exit_profile(
            getattr(trade, "strategy_version", "v1"),
            signal_level=getattr(trade, "signal_level", None),
        )
        current_profit_pct = self._profit_pct(trade, current_price)
        payload = {
            "reason": "stop_would_immediately_trigger",
            "close_reason": "SOFT_STOP_IMMEDIATE",
            "replacement_reason": replacement_reason,
            "symbol": trade.symbol,
            "trade_id": trade.trade_id,
            "stop_price": stop_price,
            "current_price": current_price,
            "decision": "close_position_market",
            **self._soft_stop_payload(
                replace(trade, soft_stop_trigger_price=stop_price),
                profile=profile,
                current_price=current_price,
                current_profit_pct=current_profit_pct,
                close_reason="SOFT_STOP_IMMEDIATE",
            ),
            **self._position_diagnostics(trade, position_match),
        }
        await self._emit_admin_debug_once(
            trade,
            event_type="STOP_SKIPPED_IMMEDIATE_TRIGGER",
            idempotency_key=(
                f"trade:{trade.trade_id}:stop_skipped_immediate:"
                f"{float(stop_price or 0.0):.8f}:{float(current_price or 0.0):.8f}"
            ),
            ts=now_ms,
            payload=payload,
        )
        log.warning(
            "[live-sync] STOP_SKIPPED_IMMEDIATE_TRIGGER trade_id=%s symbol=%s stop_price=%.8f current_price=%.8f reason=%s decision=close_position_market",
            trade.trade_id,
            trade.symbol,
            float(stop_price or 0.0),
            float(current_price or 0.0),
            replacement_reason,
        )

        closed_candidate = await self.live_broker.close_position_market(
            replace(trade, soft_stop_trigger_price=stop_price),
            now_ms=now_ms,
            reason="SOFT_STOP_IMMEDIATE",
        )
        post_close_position_match = await self._refresh_position_match(client=client, trade=closed_candidate)
        if self._position_fetch_failed(post_close_position_match):
            current = await self._mark_sync_problem(
                trade,
                now_ms=now_ms,
                reason="soft_stop_immediate",
                error=f"critical_exchange_state_unavailable:{post_close_position_match.get('reason')}",
                emit_invalid_api=False,
                error_details=post_close_position_match.get("error_details"),
            )
            return current, post_close_position_match, True

        if not post_close_position_match.get("confident_flat"):
            exchange_position_qty = None if post_close_position_match.get("ambiguous") else float(post_close_position_match.get("position_qty") or 0.0)
            current = replace(
                closed_candidate,
                status=TradeStatus.OPEN,
                qty_remaining=exchange_position_qty if exchange_position_qty is not None else float(trade.qty_remaining or 0.0),
                remaining_pct=((exchange_position_qty or 0.0) / trade.qty) if trade.qty > 0 and exchange_position_qty is not None else trade.remaining_pct,
                exchange_position_amt=exchange_position_qty,
                exchange_sync_status="soft_stop_immediate_close_not_flat",
                exchange_sync_error="exchange_position_still_open_after_soft_stop_immediate_market_close",
            )
            await self._emit_admin_debug_once(
                current,
                event_type="LIVE_CLOSE_MISMATCH_POSITION_STILL_OPEN",
                idempotency_key=f"trade:{current.trade_id}:soft_stop_immediate_position_still_open:{float(exchange_position_qty or 0.0):.8f}",
                ts=now_ms,
                payload={
                    "reason": "soft_stop_immediate_position_still_open",
                    "close_reason": "SOFT_STOP_IMMEDIATE",
                    "exchange_position_amt_after_close": exchange_position_qty,
                    "decision": "open",
                    **self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=current_profit_pct,
                        close_reason="SOFT_STOP_IMMEDIATE",
                    ),
                    **self._position_diagnostics(current, post_close_position_match),
                },
            )
            return current, post_close_position_match, True

        current = self._apply_full_close(
            closed_candidate,
            close_price=current_price,
            reason="SOFT_STOP_IMMEDIATE",
            now_ms=now_ms,
        )
        current, pnl_verified = await self._finalize_closed_trade_with_exchange_pnl(
            client=client,
            trade=current,
            now_ms=now_ms,
            close_reason="SOFT_STOP_IMMEDIATE",
            close_orders=all_orders or [],
        )
        if pnl_verified:
            await self._emit_event_once(
                trade=current,
                event_type="CLOSED",
                idempotency_key=f"trade:{current.trade_id}:closed:soft_stop_immediate",
                ts=now_ms,
                payload={
                    "side": getattr(current.side, "value", str(current.side)),
                    "reason": "SOFT_STOP_IMMEDIATE",
                    "close_price": current.close_price,
                    "realized_pnl_usd": current.realized_pnl_usd,
                    "tp_hit_count": current.tp_hit_count,
                    "stop_price": stop_price,
                    "current_price": current_price,
                    **self._soft_stop_payload(
                        current,
                        profile=profile,
                        current_price=current_price,
                        current_profit_pct=current_profit_pct,
                        close_reason="SOFT_STOP_IMMEDIATE",
                    ),
                    **self._pnl_payload(current),
                },
            )
        current = await self._cleanup_stale_exit_orders_if_confirmed_flat(
            client=client,
            trade=current,
            now_ms=now_ms,
            reason="post_close_cleanup:soft_stop_immediate",
            position_match=post_close_position_match,
        )
        return current, post_close_position_match, True

    def _soft_stop_payload(
        self,
        trade: Trade,
        *,
        profile: ExitProfile,
        current_price: float,
        current_profit_pct: float,
        close_reason: str | None,
    ) -> dict[str, Any]:
        trigger_price = self._soft_stop_price(trade)
        return {
            "strategy_version": getattr(trade, "strategy_version", "v1"),
            "signal_level": getattr(trade, "signal_level", None),
            "signal_score": getattr(trade, "signal_score", None),
            "position_size_multiplier": getattr(trade, "position_size_multiplier", None),
            "exit_profile": exit_profile_to_dict(profile),
            "tp_step_pct": profile.tp_step_pct,
            "soft_stop_enabled": True,
            "soft_stop_activation_pct": profile.soft_stop_activation_pct,
            "soft_stop_start_pct": profile.soft_stop_start_pct,
            "soft_stop_increment_pct": getattr(profile, "soft_stop_increment_pct", profile.soft_stop_hourly_increment_pct),
            "soft_stop_hourly_increment_pct": profile.soft_stop_hourly_increment_pct,
            "soft_stop_increment_interval_seconds": profile.soft_stop_increment_interval_seconds,
            "soft_stop_activation_trigger": getattr(profile, "soft_stop_activation_trigger", "price"),
            "soft_stop_current_pct": getattr(trade, "soft_stop_current_pct", None),
            "soft_stop_trigger_price": trigger_price,
            "soft_stop_activated_at": getattr(trade, "soft_stop_activated_at", None),
            "soft_stop_last_raise_at": getattr(trade, "soft_stop_last_raise_at", None),
            "soft_stop_next_raise_at": getattr(trade, "soft_stop_next_raise_at", None),
            "current_price": current_price,
            "current_profit_pct": current_profit_pct,
            "close_reason": close_reason,
            "exchange_safety_sl_price": getattr(trade, "exchange_safety_sl_price", None),
            "close_reason_code": close_reason,
        }

    def _current_price_from_position_match(self, position_match: dict[str, Any]) -> float:
        matched = position_match.get("matched_position")
        if isinstance(matched, dict):
            for key in ("markPrice", "lastPrice"):
                value = self._to_float(matched.get(key))
                if value > 0:
                    return value
        return 0.0

    def _stop_would_immediately_trigger(self, trade: Trade, stop_price: float, current_price: float) -> bool:
        stop = float(stop_price or 0.0)
        current = float(current_price or 0.0)
        if stop <= 0 or current <= 0:
            return False
        if trade.side == Side.SHORT:
            return stop <= current
        return stop >= current

    def _profit_pct(self, trade: Trade, current_price: float) -> float:
        entry_price = float(trade.entry_price or 0.0)
        if entry_price <= 0:
            return 0.0
        if trade.side == Side.SHORT:
            return ((entry_price - float(current_price)) / entry_price) * 100.0
        return ((float(current_price) - entry_price) / entry_price) * 100.0

    def _soft_stop_price(self, trade: Trade) -> float:
        entry_price = float(trade.entry_price or 0.0)
        current_pct = float(getattr(trade, "soft_stop_current_pct", 0.0) or 0.0)
        if trade.side == Side.SHORT:
            return entry_price * (1.0 - (current_pct / 100.0))
        return entry_price * (1.0 + (current_pct / 100.0))

    def _can_skip_finalized_trade(self, trade: Trade) -> bool:
        return (
            self._is_final_status(getattr(trade, "status", None))
            and bool(getattr(trade, "cleanup_completed", False))
            and bool(getattr(trade, "income_sync_completed", False))
        )

    def _is_final_status(self, status: Any) -> bool:
        try:
            status_value = TradeStatus(status)
        except Exception:
            status_value = status
        return status_value in FINAL_TRADE_STATUSES

    def _has_verified_exchange_pnl(self, trade: Trade) -> bool:
        return (
            str(getattr(trade, "mode", "")) == "live"
            and getattr(trade, "exchange_realized_pnl_usd", None) is not None
            and getattr(trade, "pnl_source", None) == "exchange"
            and str(getattr(trade, "pnl_status", "") or "") == "verified"
        )

    def _income_sync_lock_for(self, trade: Trade) -> asyncio.Lock:
        trade_id = str(getattr(trade, "trade_id", "") or "-")
        lock = self._income_sync_locks.get(trade_id)
        if lock is None:
            lock = asyncio.Lock()
            self._income_sync_locks[trade_id] = lock
        return lock

    def _income_sync_cooldown_active(self, trade: Trade, *, now_ms: int) -> bool:
        if bool(getattr(trade, "income_sync_completed", False)):
            return False
        next_retry_at = getattr(trade, "income_sync_next_retry_at", None)
        try:
            return next_retry_at is not None and int(next_retry_at) > int(now_ms)
        except (TypeError, ValueError):
            return False

    def _cached_income_backoff_active(self, cached: dict[str, Any] | None, *, now_ms: int) -> bool:
        if not isinstance(cached, dict) or cached.get("verified"):
            return False
        try:
            next_retry_at = cached.get("next_retry_at")
            return next_retry_at is not None and int(next_retry_at) > int(now_ms)
        except (TypeError, ValueError):
            return False

    def _income_sync_backoff_ms(self, attempts: int) -> int:
        try:
            index = max(0, int(attempts or 1) - 1)
        except (TypeError, ValueError):
            index = 0
        seconds = INCOME_SYNC_BACKOFF_SECONDS[min(index, len(INCOME_SYNC_BACKOFF_SECONDS) - 1)]
        return int(seconds * 1000)

    def _mark_pnl_pending_trade(
        self,
        trade: Trade,
        *,
        local_pnl: float,
        now_ms: int,
        error: str | None,
        attempts: int,
        next_retry_at: int | None = None,
    ) -> Trade:
        return replace(
            trade,
            status=TradeStatus.OPEN,
            qty_remaining=0.0,
            remaining_pct=0.0,
            exchange_position_amt=0.0,
            exchange_position_confirmed_flat=True,
            exchange_sync_status="pnl_pending",
            exchange_sync_error=error,
            local_realized_pnl_usd=local_pnl,
            local_realized_pnl_usdt=local_pnl,
            realized_pnl_usd=0.0,
            pnl_source="local",
            pnl_status="pending",
            pnl_verified_at=None,
            last_income_sync_at=now_ms,
            income_sync_completed=False,
            income_sync_attempts=max(0, int(attempts or 0)),
            income_sync_next_retry_at=next_retry_at,
            income_sync_error=error,
        )

    def _exception_details(self, exc: Exception) -> dict[str, Any]:
        if isinstance(getattr(exc, "details", None), dict):
            return dict(getattr(exc, "details", {}) or {})
        return self._exchange_error_details(exc)

    def _is_rate_limited_error(self, exc: Exception | None = None, *, details: dict[str, Any] | None = None) -> bool:
        details = dict(details or {})
        if not details and exc is not None:
            details = self._exception_details(exc)
        if details.get("http_status") == 429:
            return True
        message = str(details.get("response_body") or details.get("exchange_error_message") or exc or "")
        return "429" in message or "too many request" in message.lower() or "rate limit" in message.lower()

    async def _finalize_closed_trade_with_exchange_pnl(
        self,
        *,
        client: Any,
        trade: Trade,
        now_ms: int,
        close_reason: str,
        close_orders: list[dict[str, Any]] | None = None,
    ) -> tuple[Trade, bool]:
        if str(getattr(trade, "mode", "")) != "live":
            return trade, True
        if self._has_verified_exchange_pnl(trade):
            return replace(
                trade,
                income_sync_completed=True,
                last_income_sync_at=getattr(trade, "last_income_sync_at", None) or now_ms,
                income_sync_error=None,
            ), True

        cached_verification = self._income_result_cache.get(str(getattr(trade, "trade_id", "")))
        if isinstance(cached_verification, dict) and cached_verification.get("verified"):
            return await self._apply_exchange_pnl_verification(
                trade=trade,
                verification=cached_verification,
                local_pnl=float(
                    getattr(trade, "local_realized_pnl_usd", None)
                    if getattr(trade, "local_realized_pnl_usd", None) is not None
                    else getattr(trade, "realized_pnl_usd", 0.0) or 0.0
                ),
                now_ms=now_ms,
                close_reason=close_reason,
            ), True
        if self._cached_income_backoff_active(cached_verification, now_ms=now_ms):
            local_pnl = float(
                getattr(trade, "local_realized_pnl_usd", None)
                if getattr(trade, "local_realized_pnl_usd", None) is not None
                else getattr(trade, "realized_pnl_usd", 0.0) or 0.0
            )
            return self._mark_pnl_pending_trade(
                trade,
                local_pnl=local_pnl,
                now_ms=int(cached_verification.get("last_income_sync_at") or now_ms),
                error=str(cached_verification.get("error") or "income_sync_cooldown"),
                attempts=int(cached_verification.get("attempts") or 0),
                next_retry_at=int(cached_verification.get("next_retry_at")),
            ), False

        local_pnl = float(getattr(trade, "local_realized_pnl_usd", None) if getattr(trade, "local_realized_pnl_usd", None) is not None else getattr(trade, "realized_pnl_usd", 0.0) or 0.0)
        if self._income_sync_cooldown_active(trade, now_ms=now_ms):
            pending = self._mark_pnl_pending_trade(
                trade,
                local_pnl=local_pnl,
                now_ms=now_ms,
                error=getattr(trade, "income_sync_error", None) or "income_sync_cooldown",
                attempts=int(getattr(trade, "income_sync_attempts", 0) or 0),
            )
            return pending, False

        lock = self._income_sync_lock_for(trade)
        async with lock:
            cached_verification = self._income_result_cache.get(str(getattr(trade, "trade_id", "")))
            if isinstance(cached_verification, dict) and cached_verification.get("verified"):
                return await self._apply_exchange_pnl_verification(
                    trade=trade,
                    verification=cached_verification,
                    local_pnl=local_pnl,
                    now_ms=now_ms,
                    close_reason=close_reason,
                ), True
            if self._cached_income_backoff_active(cached_verification, now_ms=now_ms):
                return self._mark_pnl_pending_trade(
                    trade,
                    local_pnl=local_pnl,
                    now_ms=int(cached_verification.get("last_income_sync_at") or now_ms),
                    error=str(cached_verification.get("error") or "income_sync_cooldown"),
                    attempts=int(cached_verification.get("attempts") or 0),
                    next_retry_at=int(cached_verification.get("next_retry_at")),
                ), False

            if self._income_sync_cooldown_active(trade, now_ms=now_ms):
                pending = self._mark_pnl_pending_trade(
                    trade,
                    local_pnl=local_pnl,
                    now_ms=now_ms,
                    error=getattr(trade, "income_sync_error", None) or "income_sync_cooldown",
                    attempts=int(getattr(trade, "income_sync_attempts", 0) or 0),
                )
                return pending, False

            verification = await self._fetch_exchange_close_pnl_with_retries(
                client=client,
                trade=trade,
                now_ms=now_ms,
                close_orders=close_orders or [],
            )
        if not verification.get("verified"):
            attempts = int(getattr(trade, "income_sync_attempts", 0) or 0) + int(verification.get("attempts", 0) or 1)
            next_retry_at = now_ms + self._income_sync_backoff_ms(attempts)
            pending = self._mark_pnl_pending_trade(
                trade,
                local_pnl=local_pnl,
                now_ms=now_ms,
                error=str(verification.get("error") or "income_records_not_found"),
                attempts=attempts,
                next_retry_at=next_retry_at,
            )
            self._income_result_cache[str(getattr(trade, "trade_id", ""))] = {
                "verified": False,
                "error": pending.income_sync_error,
                "attempts": pending.income_sync_attempts,
                "last_income_sync_at": pending.last_income_sync_at,
                "next_retry_at": pending.income_sync_next_retry_at,
                "rate_limited": verification.get("rate_limited", False),
            }
            await self._emit_event_once(
                trade=pending,
                event_type="CLOSED_PNL_PENDING",
                idempotency_key=f"trade:{pending.trade_id}:closed:pnl_pending",
                ts=now_ms,
                payload={
                    "side": getattr(pending.side, "value", str(pending.side)),
                    "reason": close_reason,
                    "close_price": pending.close_price,
                    "local_realized_pnl_usd": local_pnl,
                    "pnl_status": "pending",
                    "pnl_source": "exchange_pending",
                    "attempts": verification.get("attempts", 0),
                    "income_sync_attempts": pending.income_sync_attempts,
                    "income_sync_next_retry_at": pending.income_sync_next_retry_at,
                    "rate_limited": verification.get("rate_limited", False),
                    "error": verification.get("error"),
                },
            )
            await self._emit_admin_debug_once(
                pending,
                event_type="LIVE_PNL_PENDING",
                idempotency_key=f"trade:{pending.trade_id}:pnl_pending:{close_reason}",
                ts=now_ms,
                payload={
                    "reason": close_reason,
                    "local_pnl": local_pnl,
                    "decision": "final_message_deferred",
                    "attempts": verification.get("attempts", 0),
                    "income_sync_attempts": pending.income_sync_attempts,
                    "income_sync_next_retry_at": pending.income_sync_next_retry_at,
                    "rate_limited": verification.get("rate_limited", False),
                    "error": verification.get("error"),
                    "matched_income_records": verification.get("matched_income_records", []),
                },
            )
            return pending, False

        self._income_result_cache[str(getattr(trade, "trade_id", ""))] = dict(verification)
        return await self._apply_exchange_pnl_verification(
            trade=trade,
            verification=verification,
            local_pnl=local_pnl,
            now_ms=now_ms,
            close_reason=close_reason,
        ), True

    async def _apply_exchange_pnl_verification(
        self,
        *,
        trade: Trade,
        verification: dict[str, Any],
        local_pnl: float,
        now_ms: int,
        close_reason: str,
    ) -> Trade:
        exchange_net_pnl = float(
            verification.get(
                "exchange_net_realized_pnl_usd",
                verification.get(
                    "exchange_final_pnl_usd",
                    verification.get("exchange_realized_pnl_usd", 0.0),
                ),
            )
            or 0.0
        )
        exchange_commission = float(verification.get("exchange_commission_usd", 0.0) or 0.0)
        exchange_gross_realized_pnl = float(
            verification.get(
                "exchange_gross_realized_pnl_usd",
                exchange_net_pnl - exchange_commission,
            )
            or 0.0
        )
        verified = replace(
            trade,
            local_realized_pnl_usd=local_pnl,
            local_realized_pnl_usdt=local_pnl,
            exchange_realized_pnl_usd=exchange_net_pnl,
            exchange_realized_pnl_usdt=exchange_net_pnl,
            exchange_gross_realized_pnl_usd=exchange_gross_realized_pnl,
            exchange_gross_realized_pnl_usdt=exchange_gross_realized_pnl,
            exchange_net_realized_pnl_usd=exchange_net_pnl,
            exchange_net_realized_pnl_usdt=exchange_net_pnl,
            realized_pnl_usd=exchange_net_pnl,
            pnl_source="exchange",
            exchange_commission_usd=exchange_commission,
            exchange_commission_usdt=exchange_commission,
            exchange_close_time=verification.get("exchange_close_time"),
            pnl_verified_at=now_ms,
            pnl_status="verified",
            last_income_sync_at=now_ms,
            income_sync_completed=True,
            income_sync_error=None,
            income_sync_next_retry_at=None,
            income_sync_attempts=int(getattr(trade, "income_sync_attempts", 0) or 0)
            + int(verification.get("attempts", 0) or 1),
            matched_income_records=verification.get("matched_income_records", []) or [],
            matched_commission_records=verification.get("matched_commission_records", []) or [],
        )
        gross_difference = abs(exchange_gross_realized_pnl - local_pnl)
        net_difference = abs(exchange_net_pnl - local_pnl)
        if gross_difference > 0.01:
            await self._emit_admin_debug_once(
                verified,
                event_type="LIVE_PNL_MISMATCH",
                idempotency_key=f"trade:{verified.trade_id}:pnl_mismatch:{close_reason}",
                ts=now_ms,
                payload={
                    "symbol": verified.symbol,
                    "trade_id": verified.trade_id,
                    "local_pnl": local_pnl,
                    "exchange_pnl": exchange_net_pnl,
                    "exchange_realized_pnl": exchange_gross_realized_pnl,
                    "exchange_gross_realized_pnl_usd": exchange_gross_realized_pnl,
                    "exchange_gross_realized_pnl": exchange_gross_realized_pnl,
                    "commission": exchange_commission,
                    "exchange_commission": exchange_commission,
                    "exchange_net_realized_pnl": exchange_net_pnl,
                    "exchange_net_realized_pnl_usd": exchange_net_pnl,
                    "final_pnl": exchange_net_pnl,
                    "difference": exchange_gross_realized_pnl - local_pnl,
                    "gross_difference": gross_difference,
                    "net_difference": net_difference,
                    "difference_local_vs_gross": exchange_gross_realized_pnl - local_pnl,
                    "difference_local_vs_net": exchange_net_pnl - local_pnl,
                    "open_time": verified.opened_at,
                    "close_time": verified.closed_at,
                    "matched_income_records": verification.get("matched_income_records", []),
                    "matched_commission_records": verification.get("matched_commission_records", []),
                    "matched_order_ids": verification.get("matched_order_ids", []),
                    "matched_order_count": len(verification.get("matched_order_ids", []) or []),
                    "suspicious_extra_order_count": verification.get("suspicious_extra_order_count", 0),
                    "reason": close_reason,
                },
            )
        elif abs(exchange_commission) > 0.00000001 or net_difference > 0.01:
            await self._emit_admin_debug_once(
                verified,
                event_type="LIVE_PNL_COMMISSION_DIFFERENCE",
                idempotency_key=f"trade:{verified.trade_id}:pnl_commission_difference:{close_reason}",
                ts=now_ms,
                payload={
                    "severity": "INFO",
                    "symbol": verified.symbol,
                    "trade_id": verified.trade_id,
                    "local_pnl": local_pnl,
                    "gross_pnl": exchange_gross_realized_pnl,
                    "exchange_gross_realized_pnl": exchange_gross_realized_pnl,
                    "commission": exchange_commission,
                    "exchange_commission": exchange_commission,
                    "net_pnl": exchange_net_pnl,
                    "exchange_net_realized_pnl": exchange_net_pnl,
                    "gross_difference": gross_difference,
                    "net_difference": net_difference,
                    "decision": "commission_only_difference",
                    "reason": close_reason,
                },
            )
        return verified

    async def _fetch_exchange_close_pnl_with_retries(
        self,
        *,
        client: Any,
        trade: Trade,
        now_ms: int,
        close_orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        attempts = 0
        last_error: str | None = None
        last_error_details: dict[str, Any] = {}
        rate_limited = False
        delays = list(self.pnl_retry_delays_seconds)
        max_attempts = max(len(delays) + 1, 1)
        index = 0
        while index < max_attempts:
            attempts += 1
            try:
                async with self._income_sync_gate:
                    result = await self._fetch_exchange_close_pnl_once(
                        client=client,
                        trade=trade,
                        now_ms=now_ms,
                        close_orders=close_orders,
                    )
                if result.get("verified"):
                    result["attempts"] = attempts
                    return result
                last_error = str(result.get("error") or "income_records_not_found")
                rate_limited = bool(result.get("rate_limited", False))
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_error_details = self._exception_details(exc)
                rate_limited = self._is_rate_limited_error(exc, details=last_error_details)
            active_delays = list(INCOME_SYNC_BACKOFF_SECONDS) if rate_limited else delays
            max_attempts = len(active_delays) + 1
            if index < len(active_delays):
                await asyncio.sleep(max(float(active_delays[index]), 0.0))
            index += 1
        return {
            "verified": False,
            "attempts": attempts,
            "error": last_error or "income_records_not_found",
            "rate_limited": rate_limited,
            "error_details": last_error_details,
        }

    async def _fetch_exchange_close_pnl_once(
        self,
        *,
        client: Any,
        trade: Trade,
        now_ms: int,
        close_orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        get_income_history = getattr(client, "get_income_history", None)
        if not callable(get_income_history):
            return {"verified": False, "error": "income_history_endpoint_unavailable"}

        start_time, end_time = self._pnl_income_time_window(trade, now_ms=now_ms)
        matched_orders = self._matched_trade_orders(
            trade,
            close_orders,
            start_time=start_time,
            end_time=end_time,
        )
        matched_order_ids = self._debug_matched_order_ids(trade, matched_orders)
        suspicious_extra_order_count = self._suspicious_extra_order_count(
            trade,
            close_orders,
            matched_orders,
            start_time=start_time,
            end_time=end_time,
        )
        income_records = await get_income_history(
            symbol=trade.symbol,
            income_type=None,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
            raise_on_error=True,
        )

        matched_realized = self._match_income_records(
            income_records or [],
            trade=trade,
            symbol=trade.symbol,
            start_time=start_time,
            end_time=end_time,
            income_type="REALIZED_PNL",
            matched_orders=matched_orders,
        )
        if not matched_realized:
            return {
                "verified": False,
                "error": "realized_pnl_income_records_not_found",
                "matched_income_records": [],
                "matched_order_ids": matched_order_ids,
                "suspicious_extra_order_count": suspicious_extra_order_count,
            }

        matched_commission = self._match_income_records(
            income_records or [],
            trade=trade,
            symbol=trade.symbol,
            start_time=start_time,
            end_time=end_time,
            income_type="COMMISSION",
            matched_orders=matched_orders,
        )
        exchange_gross_realized_pnl = self._sum_income_amounts(matched_realized)
        exchange_commission = self._sum_income_amounts(matched_commission)
        # Binance UI shows net realized PnL after commission. For live trades this
        # net exchange value is the source of truth for messages and statistics.
        exchange_final_pnl = exchange_gross_realized_pnl + exchange_commission
        exchange_close_time = max(
            [self._income_record_time(row) for row in matched_realized if self._income_record_time(row) is not None]
            or [int(getattr(trade, "closed_at", 0) or now_ms)]
        )
        return {
            "verified": True,
            "exchange_realized_pnl_usd": float(exchange_final_pnl),
            "exchange_realized_pnl_usdt": float(exchange_final_pnl),
            "exchange_final_pnl_usd": float(exchange_final_pnl),
            "exchange_final_pnl_usdt": float(exchange_final_pnl),
            "exchange_net_realized_pnl_usd": float(exchange_final_pnl),
            "exchange_net_realized_pnl_usdt": float(exchange_final_pnl),
            "exchange_gross_realized_pnl_usd": float(exchange_gross_realized_pnl),
            "exchange_gross_realized_pnl_usdt": float(exchange_gross_realized_pnl),
            "exchange_commission_usd": float(exchange_commission),
            "exchange_commission_usdt": float(exchange_commission),
            "exchange_close_time": exchange_close_time,
            "matched_income_records": self._compact_income_records(matched_realized),
            "matched_commission_records": self._compact_income_records(matched_commission),
            "matched_order_ids": matched_order_ids,
            "matched_order_count": len(matched_order_ids),
            "suspicious_extra_order_count": suspicious_extra_order_count,
        }

    def _pnl_income_time_window(self, trade: Trade, *, now_ms: int) -> tuple[int, int]:
        opened_at = int(getattr(trade, "opened_at", 0) or now_ms)
        closed_at = int(getattr(trade, "closed_at", 0) or now_ms)
        start_time = max(0, opened_at - 60_000)
        end_time = max(now_ms, closed_at) + 120_000
        if end_time < start_time:
            end_time = start_time + 120_000
        return int(start_time), int(end_time)

    def _matched_trade_orders(
        self,
        trade: Trade,
        orders: list[dict[str, Any]],
        *,
        start_time: int,
        end_time: int,
    ) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            if not self._filled_order_matches_trade_window(
                trade,
                order,
                start_time=start_time,
                end_time=end_time,
            ):
                continue
            if self._order_matches_trade_identity(trade, order):
                matched.append(dict(order))
        return matched

    def _suspicious_extra_order_count(
        self,
        trade: Trade,
        orders: list[dict[str, Any]],
        matched_orders: list[dict[str, Any]],
        *,
        start_time: int,
        end_time: int,
    ) -> int:
        matched_refs = set()
        for order in matched_orders:
            matched_refs.update(self._order_identity_refs(order))
        suspicious = 0
        for order in orders:
            if not isinstance(order, dict):
                continue
            if not self._filled_order_matches_trade_window(
                trade,
                order,
                start_time=start_time,
                end_time=end_time,
            ):
                continue
            if self._order_identity_refs(order) & matched_refs:
                continue
            if self._order_matches_trade_identity(trade, order):
                continue
            suspicious += 1
        return suspicious

    def _filled_order_matches_trade_window(
        self,
        trade: Trade,
        order: dict[str, Any],
        *,
        start_time: int,
        end_time: int,
    ) -> bool:
        if str(order.get("symbol") or "").upper() != str(trade.symbol).upper():
            return False
        expected_side = self._expected_position_side(trade)
        if not self._order_position_side_matches(order, expected_side):
            return False
        if not self._is_filled_order(order):
            return False
        order_time = self._order_timestamp(order)
        if order_time > 0 and not (start_time <= int(order_time) <= end_time):
            return False
        return True

    def _order_matches_trade_identity(self, trade: Trade, order: dict[str, Any]) -> bool:
        identity = self._trade_order_identity(trade)
        refs = self._order_identity_refs(order)
        if refs & identity["order_ids"]:
            return True
        if refs & identity["algo_ids"]:
            return True
        if refs & identity["client_ids"]:
            return True
        for ref in refs:
            if self._client_id_belongs_to_trade(trade, ref):
                return True
        return False

    def _trade_order_identity(self, trade: Trade) -> dict[str, set[str]]:
        order_ids: set[str] = set()
        algo_ids: set[str] = set()
        client_ids: set[str] = set()

        for attr in ("exchange_entry_order_id", "exchange_close_order_id"):
            value = self._string_or_none(getattr(trade, attr, None))
            if value:
                order_ids.add(value)
        for attr in ("exchange_entry_client_order_id", "exchange_close_client_order_id"):
            value = self._string_or_none(getattr(trade, attr, None))
            if value:
                client_ids.add(value)

        for value in getattr(trade, "tp_order_ids", None) or []:
            text = self._string_or_none(value)
            if text:
                order_ids.add(text)
        for value in getattr(trade, "tp_algo_ids", None) or []:
            text = self._string_or_none(value)
            if text:
                algo_ids.add(text)

        for order in [getattr(trade, "exchange_stop_order", None), *(getattr(trade, "exchange_tp_orders", None) or [])]:
            if not isinstance(order, dict):
                continue
            order_ids.update(
                value
                for value in (
                    self._string_or_none(order.get("orderId")),
                    self._string_or_none(order.get("actualOrderId")),
                )
                if value
            )
            value = self._string_or_none(order.get("algoId"))
            if value:
                algo_ids.add(value)
            client_ids.update(
                value
                for value in (
                    self._string_or_none(order.get("clientOrderId")),
                    self._string_or_none(order.get("clientAlgoId")),
                )
                if value
            )

        return {
            "order_ids": order_ids,
            "algo_ids": algo_ids,
            "client_ids": client_ids,
        }

    def _order_identity_refs(self, order: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        for key in (
            "orderId",
            "algoId",
            "actualOrderId",
            "clientOrderId",
            "clientAlgoId",
            "origClientOrderId",
            "tradeId",
        ):
            value = self._string_or_none(order.get(key))
            if value:
                refs.add(value)
        return refs

    def _client_id_belongs_to_trade(self, trade: Trade, client_id: str) -> bool:
        digest = self._trade_client_id_digest(trade)
        return bool(digest and str(client_id).startswith("mb-") and digest in str(client_id))

    def _trade_client_id_digest(self, trade: Trade) -> str:
        return hashlib.sha1(str(trade.trade_id).encode("utf-8")).hexdigest()[:20]

    def _is_filled_order(self, order: dict[str, Any]) -> bool:
        return str(order.get("status") or order.get("algoStatus") or "").upper() in {"FILLED", "FINISHED"}

    def _sum_income_amounts(self, rows: list[dict[str, Any]]) -> Decimal:
        total = Decimal("0")
        for row in rows:
            if not isinstance(row, dict):
                continue
            total += self._income_decimal(row.get("income"))
        return total

    def _income_decimal(self, value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _match_income_records(
        self,
        rows: list[dict[str, Any]],
        *,
        trade: Trade,
        symbol: str,
        start_time: int,
        end_time: int,
        income_type: str,
        matched_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        symbol_upper = str(symbol).upper()
        expected_type = str(income_type).upper()
        known_refs = self._trade_income_refs(trade, matched_orders)
        matched: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() != symbol_upper:
                continue
            row_type = str(row.get("incomeType") or row.get("income_type") or "").upper()
            if row_type and row_type != expected_type:
                continue
            row_time = self._income_record_time(row)
            if row_time is not None and not (start_time <= row_time <= end_time):
                continue
            if self._to_float(row.get("income")) == 0.0:
                continue
            row_refs = self._income_order_refs(row)
            if (
                row_refs
                and known_refs
                and self._income_has_strict_order_ref(row)
                and not self._refs_intersect(row_refs, known_refs)
            ):
                continue
            matched.append(dict(row))
        return matched

    def _trade_income_refs(self, trade: Trade, matched_orders: list[dict[str, Any]]) -> set[str]:
        identity = self._trade_order_identity(trade)
        refs = set(identity["order_ids"]) | set(identity["client_ids"]) | set(identity["algo_ids"])
        for order in matched_orders:
            refs.update(self._order_identity_refs(order))
        return {str(value) for value in refs if value}

    def _income_order_refs(self, row: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        for key in (
            "orderId",
            "order_id",
            "clientOrderId",
            "client_order_id",
            "clientAlgoId",
            "client_algo_id",
            "tradeId",
            "trade_id",
        ):
            value = self._string_or_none(row.get(key))
            if value:
                refs.add(value)
        info = self._string_or_none(row.get("info"))
        if info and info.startswith("mb-"):
            refs.add(info)
        return refs

    def _income_has_strict_order_ref(self, row: dict[str, Any]) -> bool:
        for key in (
            "orderId",
            "order_id",
            "clientOrderId",
            "client_order_id",
            "clientAlgoId",
            "client_algo_id",
        ):
            if self._string_or_none(row.get(key)):
                return True
        info = self._string_or_none(row.get("info"))
        return bool(info and info.startswith("mb-"))

    def _refs_intersect(self, row_refs: set[str], known_refs: set[str]) -> bool:
        if row_refs & known_refs:
            return True
        for row_ref in row_refs:
            for known_ref in known_refs:
                if row_ref and known_ref and (row_ref in known_ref or known_ref in row_ref):
                    return True
        return False

    def _compact_income_records(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows[:20]:
            result.append(
                {
                    "symbol": row.get("symbol"),
                    "incomeType": row.get("incomeType") or row.get("income_type"),
                    "income": row.get("income"),
                    "time": row.get("time"),
                    "tranId": row.get("tranId"),
                    "tradeId": row.get("tradeId"),
                    "orderId": row.get("orderId") or row.get("order_id"),
                    "clientOrderId": row.get("clientOrderId") or row.get("client_order_id"),
                    "clientAlgoId": row.get("clientAlgoId") or row.get("client_algo_id"),
                    "info": row.get("info"),
                }
            )
        return result

    def _income_record_time(self, row: dict[str, Any]) -> int | None:
        for key in ("time", "T", "timestamp", "transactTime"):
            value = row.get(key)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _filled_order_ids(self, orders: list[dict[str, Any]]) -> list[str]:
        order_ids: list[str] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            if not self._is_filled_order(order):
                continue
            for key in ("orderId", "algoId", "actualOrderId", "clientOrderId", "clientAlgoId"):
                value = self._string_or_none(order.get(key))
                if value and value not in order_ids:
                    order_ids.append(value)
        return order_ids

    def _debug_matched_order_ids(self, trade: Trade, matched_orders: list[dict[str, Any]]) -> list[str]:
        values = self._filled_order_ids(matched_orders)
        identity = self._trade_order_identity(trade)
        values.extend(sorted(identity["order_ids"]))
        values.extend(sorted(identity["algo_ids"]))
        values.extend(sorted(identity["client_ids"]))
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = self._string_or_none(value)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _pnl_payload(self, trade: Trade) -> dict[str, Any]:
        local_pnl = getattr(trade, "local_realized_pnl_usd", None)
        exchange_net_pnl = (
            getattr(trade, "exchange_net_realized_pnl_usd", None)
            if getattr(trade, "exchange_net_realized_pnl_usd", None) is not None
            else getattr(trade, "exchange_realized_pnl_usd", None)
        )
        exchange_gross_pnl = getattr(trade, "exchange_gross_realized_pnl_usd", None)
        exchange_commission = getattr(trade, "exchange_commission_usd", None)
        return {
            "local_realized_pnl_usd": local_pnl,
            "local_realized_pnl_usdt": local_pnl,
            "exchange_realized_pnl_usd": exchange_net_pnl,
            "exchange_realized_pnl_usdt": exchange_net_pnl,
            "exchange_gross_realized_pnl_usd": exchange_gross_pnl,
            "exchange_gross_realized_pnl_usdt": exchange_gross_pnl,
            "exchange_net_realized_pnl_usd": exchange_net_pnl,
            "exchange_net_realized_pnl_usdt": exchange_net_pnl,
            "pnl_source": getattr(trade, "pnl_source", None),
            "exchange_commission_usd": exchange_commission,
            "exchange_commission_usdt": exchange_commission,
            "exchange_close_time": getattr(trade, "exchange_close_time", None),
            "pnl_verified_at": getattr(trade, "pnl_verified_at", None),
            "pnl_status": getattr(trade, "pnl_status", None),
            "last_income_sync_at": getattr(trade, "last_income_sync_at", None),
            "income_sync_completed": getattr(trade, "income_sync_completed", None),
        }

    async def _mark_sync_problem(
        self,
        trade: Trade,
        *,
        now_ms: int,
        reason: str,
        error: str,
        emit_invalid_api: bool,
        error_details: dict[str, Any] | None = None,
    ) -> Trade:
        log.warning(
            "[live-sync] skipping trade update trade_id=%s symbol=%s reason=%s error=%s decision=%s",
            trade.trade_id,
            trade.symbol,
            reason,
            error,
            "skip_update",
        )
        current = replace(
            trade,
            exchange_last_sync_at=now_ms,
            exchange_last_sync_reason=reason,
            exchange_sync_status="error",
            exchange_sync_error=error,
        )
        if emit_invalid_api:
            await self._emit_invalid_api_once(current, now_ms=now_ms, reason=error)
        await self._emit_admin_debug_once(
            current,
            event_type="LIVE_SYNC_ERROR",
            idempotency_key=f"trade:{current.trade_id}:sync_error:{reason}:{error}",
            ts=now_ms,
            payload={
                "reason": reason,
                "error": error,
                "decision": "skip_update",
                **self._compact_error_details(error_details),
            },
        )
        return current

    async def _mark_invalid_symbol_trade(
        self,
        trade: Trade,
        *,
        now_ms: int,
        reason: str,
        invalid_reason: str,
    ) -> Trade:
        symbol = str(getattr(trade, "symbol", "") or "")
        current = replace(
            trade,
            status=TradeStatus.ERROR_INVALID_SYMBOL,
            closed_at=now_ms,
            exit_last_check_at=now_ms,
            exit_reason="ERROR_INVALID_SYMBOL",
            exchange_last_sync_at=now_ms,
            exchange_last_sync_reason=reason,
            exchange_sync_status="invalid_symbol",
            exchange_sync_error=invalid_reason,
            qty_remaining=0.0,
            remaining_pct=0.0,
            exchange_position_confirmed_flat=True,
            cleanup_completed=True,
            cleanup_completed_at=now_ms,
            income_sync_completed=True,
            last_income_sync_at=now_ms,
        )
        log.warning(
            "[live-sync] INVALID_SYMBOL_SKIPPED trade_id=%s symbol=%s user=%s reason=%s action=quarantined",
            getattr(trade, "trade_id", "-"),
            symbol,
            getattr(trade, "user_id", "-"),
            invalid_reason,
        )
        await self._emit_admin_debug_once(
            current,
            event_type="INVALID_SYMBOL_SKIPPED",
            idempotency_key=f"trade:{current.trade_id}:invalid_symbol:{invalid_reason}",
            ts=now_ms,
            payload={
                "reason": "invalid_symbol",
                "invalid_reason": invalid_reason,
                "action": "quarantined_without_binance_call",
                "trade_id": current.trade_id,
                "status": TradeStatus.ERROR_INVALID_SYMBOL.value,
            },
        )
        return current

    async def _emit_invalid_api_once(self, trade: Trade, *, now_ms: int, reason: str) -> None:
        await self._emit_event_once(
            trade=trade,
            event_type="INVALID_API",
            idempotency_key=f"trade:{trade.trade_id}:invalid_api:{reason}",
            ts=now_ms,
            payload={"reason": reason},
        )

    async def _emit_event_once(
        self,
        *,
        trade: Trade,
        event_type: str,
        idempotency_key: str,
        ts: int,
        payload: dict[str, Any],
    ) -> None:
        await self.trade_events_repo.add_event_once(
            idempotency_key=idempotency_key,
            trade_id=trade.trade_id,
            event_type=event_type,
            ts=ts,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload=payload,
        )

    async def _emit_admin_debug_once(
        self,
        trade: Trade,
        *,
        event_type: str,
        idempotency_key: str,
        ts: int,
        payload: dict[str, Any],
    ) -> None:
        await self.trade_events_repo.add_event_once(
            idempotency_key=idempotency_key,
            trade_id=trade.trade_id,
            event_type=event_type,
            ts=ts,
            symbol=trade.symbol,
            user_id=trade.user_id,
            mode=trade.mode,
            payload=payload,
        )

    async def _cleanup_stale_exit_orders_if_confirmed_flat(
        self,
        *,
        client: Any,
        trade: Trade,
        now_ms: int,
        reason: str,
        position_match: dict[str, Any] | None = None,
    ) -> Trade:
        if bool(getattr(trade, "cleanup_completed", False)) and bool(
            getattr(trade, "exchange_position_confirmed_flat", False)
        ):
            return trade

        if position_match is None:
            try:
                position_payload = await client.get_position_risk(symbol=trade.symbol)
            except Exception as exc:
                await self._emit_admin_debug_once(
                    trade,
                    event_type="LIVE_EXIT_ORDER_CLEANUP",
                    idempotency_key=f"trade:{trade.trade_id}:cleanup:{reason}:position_error",
                    ts=now_ms,
                    payload={
                        "reason": reason,
                        "cleanup_status": "skipped",
                        "cleanup_error": f"{type(exc).__name__}: {exc}",
                        "exchange_position_confirmed_flat": False,
                    },
                )
                return trade
            position_match = self._match_exchange_position(trade, position_payload)

        if position_match.get("ambiguous") or not position_match.get("confident_flat"):
            await self._emit_admin_debug_once(
                trade,
                event_type="LIVE_EXIT_ORDER_CLEANUP",
                idempotency_key=f"trade:{trade.trade_id}:cleanup:{reason}:not_flat",
                ts=now_ms,
                payload={
                    "reason": reason,
                    "cleanup_status": "skipped",
                    "exchange_position_confirmed_flat": False,
                    **self._position_diagnostics(trade, position_match),
                },
            )
            return trade

        open_orders = await self._maybe_call_list(client, "get_open_orders", symbol=trade.symbol)
        open_algo_orders = await self._maybe_call_list(client, "get_open_algo_orders", symbol=trade.symbol)
        summaries = self._summarize_open_orders([*open_orders, *open_algo_orders])
        if not summaries:
            current = replace(
                trade,
                cleanup_completed=True,
                cleanup_completed_at=now_ms,
                exchange_position_confirmed_flat=True,
            )
            log.info(
                "[live-sync] cleanup completed trade_id=%s symbol=%s reason=%s open_order_count=0 exchange_position_confirmed_flat=True",
                current.trade_id,
                current.symbol,
                reason,
            )
            await self._emit_admin_debug_once(
                current,
                event_type="LIVE_EXIT_ORDER_CLEANUP",
                idempotency_key=f"trade:{current.trade_id}:cleanup:{reason}:empty",
                ts=now_ms,
                payload={
                    "reason": reason,
                    "cleanup_status": "completed",
                    "symbol": current.symbol,
                    "local_trade_id": current.trade_id,
                    "open_order_count_before_cleanup": 0,
                    "canceled_orders": [],
                    "cleanup_errors": [],
                    "exchange_position_confirmed_flat": True,
                    **self._position_diagnostics(current, position_match),
                },
            )
            return current

        cleanup_errors: list[str] = []
        if open_orders and hasattr(client, "cancel_all_orders"):
            try:
                await client.cancel_all_orders(symbol=trade.symbol)
            except Exception as exc:
                cleanup_errors.append(f"regular:{type(exc).__name__}:{exc}")
        if open_algo_orders and hasattr(client, "cancel_all_algo_orders"):
            try:
                await client.cancel_all_algo_orders(symbol=trade.symbol)
            except Exception as exc:
                cleanup_errors.append(f"algo:{type(exc).__name__}:{exc}")

        current = replace(
            trade,
            cleanup_completed=not cleanup_errors,
            cleanup_completed_at=now_ms if not cleanup_errors else getattr(trade, "cleanup_completed_at", None),
            exchange_position_confirmed_flat=True,
        )
        log.info(
            "[live-sync] cleanup stale exit orders trade_id=%s symbol=%s reason=%s open_order_count=%s canceled_orders=%s exchange_position_confirmed_flat=True errors=%s",
            current.trade_id,
            current.symbol,
            reason,
            len(summaries),
            json.dumps(summaries, separators=(",", ":")),
            cleanup_errors,
        )

        digest = hashlib.sha1(json.dumps(summaries, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        await self._emit_admin_debug_once(
            current,
            event_type="LIVE_EXIT_ORDER_CLEANUP",
            idempotency_key=f"trade:{current.trade_id}:cleanup:{reason}:{digest}",
            ts=now_ms,
            payload={
                "reason": reason,
                "cleanup_status": "error" if cleanup_errors else "completed",
                "symbol": current.symbol,
                "local_trade_id": current.trade_id,
                "open_order_count_before_cleanup": len(summaries),
                "canceled_orders": summaries,
                "cleanup_errors": cleanup_errors,
                "exchange_position_confirmed_flat": True,
                **self._position_diagnostics(current, position_match),
            },
        )
        return current

    def _refresh_tp_orders(
        self,
        trade: Trade,
        all_orders: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        refreshed: list[dict[str, Any]] = []
        for index, order in enumerate(trade.exchange_tp_orders or [], start=1):
            stage = int(order.get("stage") or index)
            matched = self._match_order_snapshot(order, all_orders, open_orders)
            if matched is None:
                refreshed.append(dict(order))
                continue
            row = dict(order)
            row.update(
                {
                    "orderId": self._string_or_none(matched.get("orderId")) or row.get("orderId"),
                    "algoId": self._string_or_none(matched.get("algoId")) or row.get("algoId"),
                    "actualOrderId": self._string_or_none(matched.get("actualOrderId")) or row.get("actualOrderId"),
                    "clientOrderId": self._string_or_none(matched.get("clientOrderId") or matched.get("clientAlgoId"))
                    or row.get("clientOrderId"),
                    "clientAlgoId": self._string_or_none(matched.get("clientAlgoId")) or row.get("clientAlgoId"),
                    "status": self._execution_status(matched) or row.get("status"),
                    "type": self._string_or_none(matched.get("type") or matched.get("orderType")) or row.get("type"),
                    "executedQty": self._to_float(matched.get("executedQty") or matched.get("origQty") or matched.get("quantity")),
                    "avgPrice": self._to_float(matched.get("avgPrice") or matched.get("actualPrice")),
                    "stopPrice": self._to_float(matched.get("stopPrice") or matched.get("triggerPrice")),
                    "triggerPrice": self._to_float(matched.get("triggerPrice") or matched.get("stopPrice")),
                    "price": self._to_float(matched.get("price")),
                    "stage": stage,
                }
            )
            refreshed.append(row)
        return refreshed

    def _refresh_stop_order(
        self,
        trade: Trade,
        all_orders: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        order = trade.exchange_stop_order or {}
        matched = self._match_order_snapshot(order, all_orders, open_orders)
        if matched is None:
            return order or None
        row = dict(order)
        row.update(
            {
                "orderId": self._string_or_none(matched.get("orderId")) or row.get("orderId"),
                "algoId": self._string_or_none(matched.get("algoId")) or row.get("algoId"),
                "actualOrderId": self._string_or_none(matched.get("actualOrderId")) or row.get("actualOrderId"),
                "clientOrderId": self._string_or_none(matched.get("clientOrderId") or matched.get("clientAlgoId"))
                or row.get("clientOrderId"),
                "clientAlgoId": self._string_or_none(matched.get("clientAlgoId")) or row.get("clientAlgoId"),
                "status": self._execution_status(matched) or row.get("status"),
                "type": self._string_or_none(matched.get("type") or matched.get("orderType")) or row.get("type"),
                "executedQty": self._to_float(matched.get("executedQty") or matched.get("origQty") or matched.get("quantity")),
                "avgPrice": self._to_float(matched.get("avgPrice") or matched.get("actualPrice")),
                "stopPrice": self._to_float(matched.get("stopPrice") or matched.get("triggerPrice")),
                "triggerPrice": self._to_float(matched.get("triggerPrice") or matched.get("stopPrice")),
                "price": self._to_float(matched.get("price")),
            }
        )
        return row

    def _refresh_order_ids(
        self,
        trade: Trade,
        all_orders: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[str]:
        identity = self._trade_order_identity(trade)
        order_ids = set(identity["order_ids"]) | set(identity["algo_ids"])
        for source in (all_orders, open_orders):
            for order in source:
                if not isinstance(order, dict):
                    continue
                if not self._order_matches_trade_identity(trade, order):
                    continue
                for key in ("orderId", "algoId", "actualOrderId"):
                    order_id = self._string_or_none(order.get(key))
                    if order_id:
                        order_ids.add(order_id)
        return sorted(order_ids)

    def _find_tp_order(self, trade: Trade, stage: int) -> dict[str, Any] | None:
        for order in trade.exchange_tp_orders or []:
            if int(order.get("stage") or 0) == int(stage):
                return order
        return None

    def _tp_stages(self, trade: Trade) -> list[int]:
        stages: list[int] = []
        for index, order in enumerate(trade.exchange_tp_orders or [], start=1):
            try:
                stage = int(order.get("stage") or index)
            except (TypeError, ValueError):
                continue
            if stage > 0:
                stages.append(stage)
        return sorted(set(stages))

    def _final_tp_stage(self, trade: Trade) -> int:
        stages = self._tp_stages(trade)
        return max(stages) if stages else 3

    def _tp_count(self, trade: Trade) -> int:
        stored = int(getattr(trade, "tp_count", 0) or 0)
        if stored > 0:
            return stored
        stages = self._tp_stages(trade)
        return max(stages) if stages else 3

    def _tp_order_close_fraction(self, trade: Trade, order: dict[str, Any], fill_qty: float) -> float:
        raw = order.get("close_fraction")
        try:
            fraction = float(raw)
        except (TypeError, ValueError):
            fraction = 0.0
        if fraction > 0:
            return fraction

        total_qty = float(getattr(trade, "qty", 0.0) or 0.0)
        if total_qty > 0:
            return max(float(fill_qty or 0.0), 0.0) / total_qty
        return 0.0

    def _match_order_snapshot(
        self,
        target: dict[str, Any] | None,
        all_orders: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not target:
            return None
        order_id = self._string_or_none(target.get("orderId"))
        algo_id = self._string_or_none(target.get("algoId"))
        actual_order_id = self._string_or_none(target.get("actualOrderId"))
        client_order_id = self._string_or_none(target.get("clientOrderId"))
        client_algo_id = self._string_or_none(target.get("clientAlgoId"))
        for source in (open_orders, all_orders):
            for order in source:
                order_order_id = self._string_or_none(order.get("orderId"))
                order_algo_id = self._string_or_none(order.get("algoId"))
                order_actual_order_id = self._string_or_none(order.get("actualOrderId"))
                order_client_order_id = self._string_or_none(order.get("clientOrderId"))
                order_client_algo_id = self._string_or_none(order.get("clientAlgoId"))
                if order_id and order_order_id == order_id:
                    return order
                if actual_order_id and order_order_id == actual_order_id:
                    return order
                if algo_id and order_algo_id == algo_id:
                    return order
                if client_order_id and order_client_order_id == client_order_id:
                    return order
                if client_order_id and order_client_algo_id == client_order_id:
                    return order
                if client_algo_id and order_client_algo_id == client_algo_id:
                    return order
        return None

    def _has_open_stop_order(self, trade: Trade, open_orders: list[dict[str, Any]]) -> bool:
        stop_order = trade.exchange_stop_order or {}
        order_id = self._string_or_none(stop_order.get("orderId"))
        algo_id = self._string_or_none(stop_order.get("algoId"))
        client_order_id = self._string_or_none(stop_order.get("clientOrderId"))
        client_algo_id = self._string_or_none(stop_order.get("clientAlgoId"))
        if not order_id and not algo_id and not client_order_id and not client_algo_id:
            return False
        for order in open_orders:
            if order_id and self._string_or_none(order.get("orderId")) == order_id:
                return True
            if algo_id and self._string_or_none(order.get("algoId")) == algo_id:
                return True
            if client_order_id and self._string_or_none(order.get("clientOrderId")) == client_order_id:
                return True
            if client_order_id and self._string_or_none(order.get("clientAlgoId")) == client_order_id:
                return True
            if client_algo_id and self._string_or_none(order.get("clientAlgoId")) == client_algo_id:
                return True
        return False

    def _replace_open_stop_snapshot(
        self,
        open_orders: list[dict[str, Any]],
        stop_order: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not stop_order:
            return open_orders
        stop_id = self._string_or_none(stop_order.get("orderId"))
        stop_algo_id = self._string_or_none(stop_order.get("algoId"))
        rows = [
            order
            for order in open_orders
            if not self._same_order_snapshot(order, order_id=stop_id, algo_id=stop_algo_id)
        ]
        rows.append(dict(stop_order))
        return rows

    async def _repair_remaining_position_protection(
        self,
        trade: Trade,
        *,
        client: Any | None = None,
        new_stop_price: float,
        now_ms: int,
        reason: str,
        current_open_orders: list[dict[str, Any]] | None = None,
        position_match: dict[str, Any] | None = None,
        all_orders: list[dict[str, Any]] | None = None,
    ) -> Trade:
        current_price = self._current_price_from_position_match(position_match or {})
        if (
            client is not None
            and position_match is not None
            and self._stop_would_immediately_trigger(trade, new_stop_price, current_price)
        ):
            current, _, _ = await self._close_by_immediate_stop(
                client=client,
                trade=trade,
                position_match=position_match,
                now_ms=now_ms,
                current_price=current_price,
                stop_price=float(new_stop_price),
                replacement_reason=reason,
                all_orders=all_orders or current_open_orders or [],
            )
            return current

        repair_method = getattr(self.live_broker, "repair_exit_orders_for_remaining_position", None)
        if callable(repair_method):
            try:
                return await repair_method(
                    trade,
                    new_stop_price=new_stop_price,
                    now_ms=now_ms,
                    reason=reason,
                    current_open_orders=current_open_orders,
                )
            except Exception as exc:
                log.exception(
                    "[live-sync] exit order repair failed; falling back to stop replacement trade_id=%s symbol=%s reason=%s error=%s:%s",
                    trade.trade_id,
                    trade.symbol,
                    reason,
                    type(exc).__name__,
                    exc,
                )
                await self._emit_admin_debug_once(
                    trade,
                    event_type="LIVE_PROTECTION_REPAIR_FAILED",
                    idempotency_key=f"trade:{trade.trade_id}:protection_repair_failed:{reason}:{now_ms}",
                    ts=now_ms,
                    payload={
                        "reason": reason,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "decision": "fallback_replace_stop",
                    },
                )

        return await self.live_broker.replace_stop_order(
            trade,
            new_stop_price=new_stop_price,
            now_ms=now_ms,
            current_open_orders=current_open_orders,
        )

    def _summarize_open_orders(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            summaries.append(
                {
                    "orderId": self._string_or_none(order.get("orderId") or order.get("actualOrderId")),
                    "algoId": self._string_or_none(order.get("algoId")),
                    "clientOrderId": self._string_or_none(order.get("clientOrderId") or order.get("clientAlgoId")),
                    "type": self._string_or_none(order.get("type") or order.get("orderType")),
                    "status": self._string_or_none(order.get("status") or order.get("algoStatus")),
                }
            )
        return summaries

    async def _maybe_call_list(self, client: Any, method_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        method = getattr(client, method_name, None)
        if method is None:
            return []
        rows = await method(**kwargs)
        return rows if isinstance(rows, list) else []

    async def _call_critical_list(self, client: Any, method_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        method = getattr(client, method_name, None)
        if method is None:
            return []
        rows = await method(**kwargs)
        if rows is None:
            raise RuntimeError(f"{method_name}_unavailable")
        if not isinstance(rows, list):
            raise RuntimeError(f"{method_name}_invalid_payload")
        return rows

    def _execution_status(self, order: dict[str, Any]) -> str | None:
        status = self._string_or_none(order.get("status"))
        if status:
            return status
        algo_status = self._string_or_none(order.get("algoStatus"))
        if algo_status in {"TRIGGERED", "FINISHED"}:
            return "FILLED"
        return algo_status

    def _same_order_snapshot(
        self,
        order: dict[str, Any],
        *,
        order_id: str | None,
        algo_id: str | None,
    ) -> bool:
        if order_id and self._string_or_none(order.get("orderId")) == order_id:
            return True
        if algo_id and self._string_or_none(order.get("algoId")) == algo_id:
            return True
        return False

    async def _refresh_position_match(self, *, client: Any, trade: Trade) -> dict[str, Any]:
        try:
            position_payload = await client.get_position_risk(symbol=trade.symbol, raise_on_error=True)
        except Exception as exc:
            error_details = self._exchange_error_details(exc)
            return {
                "ambiguous": True,
                "confident_flat": False,
                "position_qty": float(getattr(trade, "exchange_position_amt", 0.0) or 0.0),
                "position_amt": float(getattr(trade, "exchange_position_amt", 0.0) or 0.0),
                "expected_position_side": self._expected_position_side(trade),
                "raw_positions_for_symbol": [],
                "matched_position": None,
                "matched_position_amt": None,
                "matched_position_side": None,
                "reason": f"position_refresh_failed:{type(exc).__name__}:{exc}",
                "error_details": error_details,
            }
        return self._match_exchange_position(trade, position_payload)

    def _exchange_error_details(self, exc: Exception) -> dict[str, Any]:
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            return dict(details)
        cause = getattr(exc, "__cause__", None)
        details = getattr(cause, "details", None)
        if isinstance(details, dict):
            return dict(details)
        return {}

    def _compact_error_details(self, details: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(details, dict):
            return {}
        keys = (
            "http_status",
            "http_path",
            "exchange_error_code",
            "exchange_error_message",
            "response_body",
            "operation",
            "symbol",
            "local_time_ms",
            "binance_server_time_ms",
            "server_time_offset_ms",
            "safety_margin_ms",
            "final_timestamp",
            "recvWindow",
            "retry_after_time_sync",
            "offset_before",
            "offset_after",
            "local_before",
            "local_after",
            "round_trip_ms",
            "estimated_local_at_response",
            "retry_used_offset",
            "lock_wait_ms",
        )
        return {key: details.get(key) for key in keys if details.get(key) is not None}

    def _position_fetch_failed(self, position_match: dict[str, Any]) -> bool:
        if not bool(position_match.get("ambiguous")):
            return False
        reason = str(position_match.get("reason") or "")
        return reason.startswith("position_refresh_failed:")

    async def _emit_partial_close_detected(
        self,
        trade: Trade,
        *,
        source: str,
        stage: int | None,
        now_ms: int,
        planned_close_qty: float,
        actual_executed_qty: float,
        exchange_position_qty: float | None,
        position_match: dict[str, Any],
        local_qty_remaining_before: float | None = None,
        local_qty_remaining_after_without_exchange: float | None = None,
    ) -> None:
        local_qty_remaining = float(getattr(trade, "qty_remaining", 0.0) or 0.0)
        if local_qty_remaining_before is None:
            local_qty_remaining_before = local_qty_remaining
        if local_qty_remaining_after_without_exchange is None:
            local_qty_remaining_after_without_exchange = local_qty_remaining
        exchange_qty = exchange_position_qty if exchange_position_qty is not None else position_match.get("position_qty")
        try:
            exchange_qty_float = float(exchange_qty)
            qty_difference = abs(local_qty_remaining - exchange_qty_float)
        except (TypeError, ValueError):
            exchange_qty_float = 0.0
            qty_difference = None
        exchange_remaining_percent = (exchange_qty_float / trade.qty) if trade.qty > 0 else 0.0
        local_remaining_percent = (local_qty_remaining / trade.qty) if trade.qty > 0 else 0.0

        log.warning(
            "[live-sync] partial close detected trade_id=%s symbol=%s source=%s stage=%s planned_close_qty=%s actual_executed_qty=%s exchange_position_amt=%s local_qty_remaining=%s difference=%s",
            trade.trade_id,
            trade.symbol,
            source,
            stage,
            planned_close_qty,
            actual_executed_qty,
            exchange_qty,
            local_qty_remaining,
            qty_difference,
        )
        payload = {
            "reason": "exchange_position_not_flat_after_close_order",
            "source": source,
            "tp_index": stage,
            "tp_count": self._tp_count(trade),
            "planned_close_qty": planned_close_qty,
            "actual_executed_qty": actual_executed_qty,
            "exchange_position_amt_after_close": exchange_qty,
            "local_qty_remaining_before": local_qty_remaining_before,
            "local_qty_remaining_after_without_exchange": local_qty_remaining_after_without_exchange,
            "local_qty_remaining_after": local_qty_remaining,
            "local_qty_remaining": local_qty_remaining,
            "local_remaining_percent": local_remaining_percent,
            "exchange_remaining_percent": exchange_remaining_percent,
            "qty_difference": qty_difference,
            "decision": "open",
            "trade_closed": False,
            **self._position_diagnostics(trade, position_match),
        }
        await self._emit_admin_debug_once(
            trade,
            event_type="LIVE_PARTIAL_CLOSE_DETECTED",
            idempotency_key=(
                f"trade:{trade.trade_id}:partial_close:{source}:"
                f"{stage if stage is not None else 'na'}:{float(exchange_qty or 0.0):.8f}"
            ),
            ts=now_ms,
            payload=payload,
        )
        if exchange_qty_float > 1e-12 and float(local_qty_remaining_after_without_exchange or 0.0) <= 1e-12:
            await self._emit_admin_debug_once(
                trade,
                event_type="LIVE_CLOSE_MISMATCH_POSITION_STILL_OPEN",
                idempotency_key=(
                    f"trade:{trade.trade_id}:close_mismatch_position_still_open:{source}:"
                    f"{stage if stage is not None else 'na'}:{exchange_qty_float:.8f}"
                ),
                ts=now_ms,
                payload=payload,
            )

    def _local_remaining_after_fill(self, trade: Trade, fill_qty: float) -> float:
        reduced_qty = min(float(trade.qty_remaining or 0.0), max(fill_qty, 0.0))
        return max(float(trade.qty_remaining or 0.0) - reduced_qty, 0.0)

    def _apply_tp_fill(
        self,
        trade: Trade,
        *,
        stage: int,
        fill_qty: float,
        fill_price: float,
        exchange_position_qty: float | None = None,
    ) -> Trade:
        reduced_qty = min(float(trade.qty_remaining or 0.0), max(fill_qty, 0.0))
        local_remaining_qty = max(float(trade.qty_remaining or 0.0) - reduced_qty, 0.0)
        remaining_qty = max(float(exchange_position_qty), 0.0) if exchange_position_qty is not None else local_remaining_qty
        pnl = self._realized_pnl(trade.side, trade.entry_price, fill_price, reduced_qty)
        local_pnl_before = float(
            getattr(trade, "local_realized_pnl_usd", None)
            if getattr(trade, "local_realized_pnl_usd", None) is not None
            else float(trade.realized_pnl_usd or 0.0)
        )

        if trade.side == Side.LONG:
            new_sl = trade.entry_price if stage == 1 else trade.entry_price * 1.002
        else:
            new_sl = trade.entry_price if stage == 1 else trade.entry_price * 0.998
        local_realized_pnl = local_pnl_before + pnl

        return replace(
            trade,
            tp_hit_count=stage,
            qty_remaining=remaining_qty,
            remaining_pct=(remaining_qty / trade.qty) if trade.qty > 0 else 0.0,
            realized_pnl_usd=local_realized_pnl,
            local_realized_pnl_usd=local_realized_pnl,
            local_realized_pnl_usdt=local_realized_pnl,
            sl_price=new_sl,
            exchange_position_amt=remaining_qty,
        )

    def _apply_partial_close_fill(
        self,
        trade: Trade,
        *,
        fill_qty: float,
        fill_price: float,
        exchange_position_qty: float | None = None,
    ) -> Trade:
        reduced_qty = min(float(trade.qty_remaining or 0.0), max(fill_qty, 0.0))
        local_remaining_qty = max(float(trade.qty_remaining or 0.0) - reduced_qty, 0.0)
        remaining_qty = max(float(exchange_position_qty), 0.0) if exchange_position_qty is not None else local_remaining_qty
        pnl = self._realized_pnl(trade.side, trade.entry_price, fill_price, reduced_qty)
        local_pnl_before = float(
            getattr(trade, "local_realized_pnl_usd", None)
            if getattr(trade, "local_realized_pnl_usd", None) is not None
            else float(trade.realized_pnl_usd or 0.0)
        )
        local_realized_pnl = local_pnl_before + pnl
        return replace(
            trade,
            qty_remaining=remaining_qty,
            remaining_pct=(remaining_qty / trade.qty) if trade.qty > 0 else 0.0,
            realized_pnl_usd=local_realized_pnl,
            local_realized_pnl_usd=local_realized_pnl,
            local_realized_pnl_usdt=local_realized_pnl,
            exchange_position_amt=remaining_qty,
        )

    def _apply_full_close(self, trade: Trade, *, close_price: float, reason: str, now_ms: int) -> Trade:
        remaining_qty = float(trade.qty_remaining or 0.0)
        pnl = self._realized_pnl(trade.side, trade.entry_price, close_price, remaining_qty)
        local_pnl_before = float(
            getattr(trade, "local_realized_pnl_usd", None)
            if getattr(trade, "local_realized_pnl_usd", None) is not None
            else float(trade.realized_pnl_usd or 0.0)
        )
        local_realized_pnl = local_pnl_before + pnl
        tp_hit_count = trade.tp_hit_count
        tp_stage = self._tp_stage_from_close_reason(reason)
        if tp_stage is not None:
            tp_hit_count = tp_stage
        return replace(
            trade,
            status=TradeStatus.CLOSED,
            closed_at=now_ms,
            close_price=close_price,
            exit_reason=reason,
            tp_hit_count=tp_hit_count,
            qty_remaining=0.0,
            remaining_pct=0.0,
            realized_pnl_usd=local_realized_pnl,
              local_realized_pnl_usd=local_realized_pnl,
              local_realized_pnl_usdt=local_realized_pnl,
              exchange_position_amt=0.0,
              exchange_position_confirmed_flat=True,
          )

    def _tp_stage_from_close_reason(self, reason: str) -> int | None:
        text = str(reason or "")
        if not (text.startswith("TP") and text.endswith("_HIT")):
            return None
        try:
            stage = int(text[2:-4])
        except (TypeError, ValueError):
            return None
        return stage if stage > 0 else None

    def _external_flat_close_price(self, trade: Trade, orders: list[dict[str, Any]]) -> tuple[float, str]:
        fallback = float(trade.close_price or trade.sl_price or trade.entry_price)
        close_side = "SELL" if trade.side == Side.LONG else "BUY"
        expected_position_side = self._expected_position_side(trade)
        candidates: list[tuple[float, float]] = []

        for order in orders:
            if not isinstance(order, dict):
                continue
            if str(order.get("symbol") or trade.symbol).upper() != str(trade.symbol).upper():
                continue
            if str(order.get("status") or "").upper() != "FILLED":
                continue
            if str(order.get("side") or "").upper() != close_side:
                continue
            if not self._order_position_side_matches(order, expected_position_side):
                continue
            fill_qty = self._to_float(
                order.get("executedQty")
                or order.get("cumQty")
                or order.get("origQty")
                or order.get("quantity")
            )
            if fill_qty <= 0:
                continue
            fill_price = self._order_fill_price(order, fallback=0.0)
            if fill_price <= 0:
                continue
            candidates.append((self._order_timestamp(order), fill_price))

        if not candidates:
            return fallback, "fallback"
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1], "exchange_filled_close_order"

    def _realized_pnl(self, side: Side, entry_price: float, exit_price: float, qty: float) -> float:
        if side == Side.LONG:
            return (float(exit_price) - float(entry_price)) * float(qty)
        return (float(entry_price) - float(exit_price)) * float(qty)

    def _match_exchange_position(
        self,
        trade: Trade,
        position_payload: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        rows = self._position_rows_for_symbol(position_payload, trade.symbol)
        expected_side = self._expected_position_side(trade)

        if not expected_side:
            return {
                "ambiguous": True,
                "reason": "expected_position_side_missing",
                "position_qty": float(getattr(trade, "exchange_position_amt", 0.0) or 0.0),
                "confident_flat": False,
                "expected_position_side": None,
                "raw_positions_for_symbol": rows,
                "matched_position": None,
                "matched_position_amt": None,
                "matched_position_side": None,
            }

        if not rows:
            return {
                "ambiguous": True,
                "reason": "position_rows_missing_for_symbol",
                "position_qty": float(getattr(trade, "exchange_position_amt", 0.0) or 0.0),
                "confident_flat": False,
                "expected_position_side": expected_side,
                "raw_positions_for_symbol": [],
                "matched_position": None,
                "matched_position_amt": None,
                "matched_position_side": None,
            }

        matched_rows = [
            row
            for row in rows
            if self._position_side_matches(row, expected_side)
        ]
        if not matched_rows:
            return {
                "ambiguous": True,
                "reason": "expected_position_record_missing",
                "position_qty": float(getattr(trade, "exchange_position_amt", 0.0) or 0.0),
                "confident_flat": False,
                "expected_position_side": expected_side,
                "raw_positions_for_symbol": rows,
                "matched_position": None,
                "matched_position_amt": None,
                "matched_position_side": None,
            }

        matched = self._choose_matched_position_row(matched_rows)
        matched_amt = self._to_float(matched.get("positionAmt"))
        matched_side = self._position_side(matched) or expected_side
        position_qty = abs(matched_amt)

        return {
            "ambiguous": False,
            "reason": "matched",
            "position_qty": position_qty,
            "confident_flat": position_qty <= 1e-12,
            "expected_position_side": expected_side,
            "raw_positions_for_symbol": rows,
            "matched_position": matched,
            "matched_position_amt": matched_amt,
            "matched_position_side": matched_side,
        }

    def _position_diagnostics(self, trade: Trade, position_match: dict[str, Any]) -> dict[str, Any]:
        return {
            "local_trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "local_side": getattr(trade.side, "value", str(trade.side)),
            "local_exchange_position_mode": getattr(trade, "exchange_position_mode", None),
            "local_exchange_position_side": getattr(trade, "exchange_position_side", None),
            "expected_position_side": position_match.get("expected_position_side"),
            "raw_exchange_positions_for_symbol": position_match.get("raw_positions_for_symbol"),
            "matched_exchange_record": position_match.get("matched_position"),
            "matched_positionAmt": position_match.get("matched_position_amt"),
            "matched_positionSide": position_match.get("matched_position_side"),
        }

    def _position_rows_for_symbol(
        self,
        position_payload: dict[str, Any] | list[dict[str, Any]] | None,
        symbol: str,
    ) -> list[dict[str, Any]]:
        symbol_upper = str(symbol).upper()
        if position_payload is None:
            return []
        rows = position_payload if isinstance(position_payload, list) else [position_payload]
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("symbol", "")).upper()
            if row_symbol and row_symbol != symbol_upper:
                continue
            result.append(dict(row))
        return result

    def _expected_position_side(self, trade: Trade) -> str | None:
        mode = str(getattr(trade, "exchange_position_mode", "") or "").strip().lower()
        stored_side = self._string_or_none(getattr(trade, "exchange_position_side", None))
        if stored_side:
            stored_side = stored_side.upper()
        if mode == "one_way":
            return "BOTH"
        if mode == "hedge":
            if stored_side in {"LONG", "SHORT"}:
                return stored_side
            return "LONG" if trade.side == Side.LONG else "SHORT"
        if stored_side in {"BOTH", "LONG", "SHORT"}:
            return stored_side
        return None

    def _position_side_matches(self, row: dict[str, Any], expected_side: str) -> bool:
        row_side = self._position_side(row)
        if expected_side == "BOTH":
            return row_side in {None, "", "BOTH"}
        return row_side == expected_side

    def _order_position_side_matches(self, order: dict[str, Any], expected_side: str | None) -> bool:
        if not expected_side:
            return True
        row_side = self._position_side(order)
        if expected_side == "BOTH":
            return row_side in {None, "", "BOTH"}
        return row_side == expected_side

    def _position_side(self, row: dict[str, Any]) -> str | None:
        value = self._string_or_none(row.get("positionSide") or row.get("position_side"))
        return value.upper() if value else None

    def _choose_matched_position_row(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        for row in rows:
            if abs(self._to_float(row.get("positionAmt"))) > 1e-12:
                return row
        return rows[0]

    def _position_qty(self, position: dict[str, Any] | list[dict[str, Any]] | None) -> float:
        if position is None:
            return 0.0
        if isinstance(position, list):
            for row in position:
                amount = self._to_float(row.get("positionAmt"))
                if abs(amount) > 1e-12:
                    return abs(amount)
            return 0.0
        return abs(self._to_float(position.get("positionAmt")))

    def _order_fill_price(self, order: dict[str, Any], *, fallback: float) -> float:
        avg_price = self._to_float(order.get("avgPrice"))
        if avg_price > 0:
            return avg_price
        actual_price = self._to_float(order.get("actualPrice"))
        if actual_price > 0:
            return actual_price
        stop_price = self._to_float(order.get("stopPrice"))
        if stop_price > 0:
            return stop_price
        trigger_price = self._to_float(order.get("triggerPrice"))
        if trigger_price > 0:
            return trigger_price
        price = self._to_float(order.get("price"))
        if price > 0:
            return price
        return float(fallback)

    def _order_timestamp(self, order: dict[str, Any]) -> float:
        for key in ("updateTime", "time", "workingTime", "createTime"):
            value = self._to_float(order.get(key))
            if value > 0:
                return value
        return 0.0

    def _extract_stream_symbol(self, payload: dict[str, Any]) -> str | None:
        event_symbol = self._string_or_none(payload.get("s"))
        if event_symbol:
            return event_symbol.upper()
        data = payload.get("o") or {}
        nested_symbol = self._string_or_none(data.get("s"))
        return nested_symbol.upper() if nested_symbol else None

    def _is_open(self, trade: Trade) -> bool:
        status = trade.status.value if hasattr(trade.status, "value") else str(trade.status)
        return status == TradeStatus.OPEN.value

    def _string_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _to_float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
