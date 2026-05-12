from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any
import httpx

from meowbot.core.domain.enums import Side
from meowbot.core.domain.types import Trade
from meowbot.core.services.execution.exit.exit_profile import (
    ExitProfile,
    exit_profile_to_dict,
    get_exit_profile,
)


log = logging.getLogger("meowbot")

MIN_TP_LEVELS = 1
MAX_TP_LEVELS = 4
MIN_TP_NOTIONAL = 0.20
SMALL_MARGIN_SINGLE_TP_THRESHOLD_USDT = 1.0


@dataclass(frozen=True)
class BinanceLiveBrokerConfig:
    tp_levels: tuple[float, ...] = (0.005, 0.010, 0.015, 0.020)
    initial_stop_loss_pct: float = 0.02
    min_notional_buffer_pct: float = 0.02
    min_tp_notional: float = MIN_TP_NOTIONAL
    min_tp_levels: int = MIN_TP_LEVELS
    max_tp_levels: int = MAX_TP_LEVELS
    small_margin_single_tp_threshold_usdt: float = SMALL_MARGIN_SINGLE_TP_THRESHOLD_USDT
    order_status_poll_attempts: int = 3
    order_status_poll_delay_seconds: float = 0.7


class BinanceLiveBrokerAsync:
    def __init__(
        self,
        *,
        account_provider,
        config: BinanceLiveBrokerConfig | None = None,
    ) -> None:
        self.account_provider = account_provider
        self.config = config or BinanceLiveBrokerConfig()
        self._symbol_rules_cache: dict[str, dict[str, float]] = {}
        self._stop_replacement_locks: dict[str, asyncio.Lock] = {}
        self._cancelled_stop_algo_keys: set[str] = set()
        self._stop_replacement_cache: dict[str, dict[str, Any]] = {}

    async def ping(self) -> bool:
        return True

    async def open_position(self, trade: Trade) -> Trade:
        client, key_row, error = await self.account_provider.build_client(
            runtime_user_id=str(trade.user_id),
            require_active=True,
        )
        if client is None:
            log.info(
                "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=api_client reason=%s",
                trade.symbol,
                trade.user_id,
                error or "live_api_unavailable",
            )
            raise RuntimeError(error or "live_api_unavailable")

        try:
            exit_profile = self._exit_profile(trade)
            permissions = self._normalize_mapping((key_row or {}).get("permissions_json"))
            if not bool(permissions.get("enableFutures", False)):
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=api_permissions reason=enableFutures_required",
                    trade.symbol,
                    trade.user_id,
                )
                raise RuntimeError("enableFutures_required")

            position_context = await self._resolve_position_context(client=client, trade=trade)
            try:
                await self._cleanup_stale_exit_orders_before_entry(
                    client=client,
                    trade=trade,
                    position_context=position_context,
                )
            except Exception as exc:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=pre_entry_cleanup reason=%s",
                    trade.symbol,
                    trade.user_id,
                    self._sanitize_error_text(str(exc)),
                )
                raise
            try:
                await client.ensure_cross_margin(symbol=trade.symbol)
            except Exception as exc:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=margin_type reason=%s",
                    trade.symbol,
                    trade.user_id,
                    self._sanitize_error_text(str(exc)),
                )
                raise
            try:
                await self._ensure_entry_leverage(client=client, trade=trade)
            except Exception as exc:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=leverage reason=%s",
                    trade.symbol,
                    trade.user_id,
                    self._sanitize_error_text(str(exc)),
                )
                raise

            rules = await self._get_symbol_rules(client, trade.symbol)
            latest_entry_price = float(trade.entry_price or 0.0)
            if latest_entry_price <= 0:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=entry_price reason=invalid_entry_price",
                    trade.symbol,
                    trade.user_id,
                )
                raise RuntimeError("invalid_entry_price")

            requested_qty = float(trade.qty_requested or trade.qty or 0.0)
            entry_guard = await self._finalize_entry_submission(
                client=client,
                trade=trade,
                rules=rules,
                raw_qty=requested_qty,
                latest_entry_price=latest_entry_price,
            )
            entry_qty = float(entry_guard["rounded_qty_after_bump"])
            if entry_qty <= 0:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=symbol_filters reason=entry_qty_below_min_step",
                    trade.symbol,
                    trade.user_id,
                )
                raise RuntimeError("entry_qty_below_min_step")
            if entry_qty < rules["min_qty"]:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=symbol_filters reason=entry_qty_below_min_qty qty=%s min_qty=%s",
                    trade.symbol,
                    trade.user_id,
                    self._format_number(entry_qty),
                    self._format_number(rules["min_qty"]),
                )
                raise RuntimeError(
                    "entry_qty_below_min_qty:"
                    f"qty={self._format_number(entry_qty)};"
                    f"min_qty={self._format_number(rules['min_qty'])}"
                )

            entry_notional = entry_qty * latest_entry_price
            minimum_market_notional = self._market_entry_target_notional(float(rules["min_notional"]))
            if minimum_market_notional > 0 and entry_notional < minimum_market_notional:
                log.info(
                    "[LIVE_ENTRY_SKIPPED] symbol=%s user_id=%s gate=min_notional reason=entry_notional_below_min_notional notional=%s min_notional=%s target_notional=%s",
                    trade.symbol,
                    trade.user_id,
                    self._format_number(entry_notional),
                    self._format_number(rules["min_notional"]),
                    self._format_number(minimum_market_notional),
                )
                raise RuntimeError(
                    "entry_notional_below_min_notional:"
                    f"notional={self._format_number(entry_notional)};"
                    f"min_notional={self._format_number(rules['min_notional'])};"
                    f"min_notional_buffer_pct={self._format_number(self._min_notional_buffer_pct())};"
                    f"target_notional={self._format_number(minimum_market_notional)};"
                    f"qty={self._format_number(entry_qty)};"
                    f"entry_price={self._format_number(latest_entry_price)}"
                )

            entry_qty_text = self._format_exchange_number(entry_qty, rules["qty_step"])
            self._log_precision_details(
                symbol=trade.symbol,
                order_kind="entry_market",
                raw_qty=requested_qty,
                rounded_qty=entry_qty,
                step_size=rules["qty_step"],
            )

            entry_client_order_id = self._build_client_order_id("e", trade.trade_id)
            entry_side = self._entry_order_side(trade.side)
            log.info(
                "[LIVE_ORDER_ATTEMPT] symbol=%s user_id=%s side=%s qty=%s leverage=%s stake=%s entry_price=%s rule_id=%s strategy_version=%s positionSide=%s",
                trade.symbol,
                trade.user_id,
                entry_side,
                entry_qty_text,
                trade.leverage,
                trade.stake_usd,
                latest_entry_price,
                trade.model_id,
                getattr(trade, "strategy_version", None),
                position_context["position_side"],
            )
            try:
                entry_order = await client.create_order(
                    symbol=trade.symbol.upper(),
                    side=entry_side,
                    type="MARKET",
                    quantity=entry_qty_text,
                    positionSide=position_context["position_side"],
                    newClientOrderId=entry_client_order_id,
                    newOrderRespType="RESULT",
                )
            except Exception as exc:
                details = self._http_exception_details(exc)
                log.error(
                    "[LIVE_ORDER_REJECTED] symbol=%s user_id=%s http_status=%s exchange_code=%s exchange_message=%s path=%s body=%s",
                    trade.symbol,
                    trade.user_id,
                    details.get("http_status"),
                    details.get("exchange_error_code"),
                    details.get("exchange_error_message") or details.get("error"),
                    details.get("http_path"),
                    self._http_exception_response_body(exc),
                )
                raise
            log.info(
                "[LIVE_ORDER_ACCEPTED] symbol=%s user_id=%s order_id=%s client_order_id=%s status=%s",
                trade.symbol,
                trade.user_id,
                self._string_or_none(entry_order.get("orderId")),
                self._string_or_none(entry_order.get("clientOrderId") or entry_client_order_id),
                self._string_or_none(entry_order.get("status")),
            )
            entry_order = await self._ensure_terminal_entry_status(
                client,
                trade=trade,
                entry_order=entry_order,
            )

            executed_qty = self._to_float(
                entry_order.get("executedQty")
                or entry_order.get("cumQty")
                or entry_qty
            )
            executed_qty = self._round_qty_down(executed_qty, rules["qty_step"])
            if executed_qty <= 0:
                raise RuntimeError("entry_not_filled")

            avg_entry_price = self._extract_avg_price(entry_order, fallback=trade.entry_price)
            actual_position_qty = await self._resolve_actual_position_qty(
                client=client,
                trade=trade,
                position_context=position_context,
                executed_qty=executed_qty,
                qty_step=rules["qty_step"],
            )
            log.info(
                "[LIVE_POSITION_CONFIRMED] symbol=%s user_id=%s position_amt=%s entry_price=%s positionSide=%s",
                trade.symbol,
                trade.user_id,
                actual_position_qty,
                avg_entry_price,
                position_context["position_side"],
            )
            initial_stop_raw_price, initial_stop_price = self._compute_stop_price_parts(
                side=trade.side,
                entry_price=avg_entry_price,
                stage=0,
                explicit_sl_price=trade.sl_price,
                tick_size=rules["tick_size"],
                initial_stop_loss_pct=exit_profile.initial_sl_pct / 100.0,
            )
            protection = await self._setup_entry_protection(
                client=client,
                trade=trade,
                position_context=position_context,
                rules=rules,
                actual_position_qty=actual_position_qty,
                entry_price=avg_entry_price,
                initial_stop_raw_price=initial_stop_raw_price,
                initial_stop_price=initial_stop_price,
                entry_order_id=self._string_or_none(entry_order.get("orderId")),
            )
            order_ids = self._dedupe_order_ids(
                [
                    self._order_identifier(entry_order),
                    *list(protection["order_ids"]),
                ]
            )

            return replace(
                trade,
                entry_price=avg_entry_price,
                sl_price=initial_stop_price,
                qty=actual_position_qty,
                qty_filled=actual_position_qty,
                qty_remaining=actual_position_qty,
                remaining_pct=1.0,
                mode="live",
                execution_engine="binance_futures_live",
                exchange_name="binance",
                exchange_entry_order_id=self._string_or_none(entry_order.get("orderId")),
                exchange_entry_client_order_id=self._string_or_none(entry_order.get("clientOrderId") or entry_client_order_id),
                exchange_entry_status=self._string_or_none(entry_order.get("status")),
                exchange_avg_entry_price=avg_entry_price,
                exchange_position_mode=position_context["position_mode"],
                exchange_position_side=position_context["position_side"],
                exchange_stop_order=protection["stop_order"],
                exchange_tp_orders=list(protection["tp_orders"]),
                exchange_order_ids=order_ids,
                tp_count=int(protection["tp_count"]),
                tp_levels=list(protection["tp_levels"]),
                tp_close_fractions=list(protection["tp_close_fractions"]),
                tp_plan=list(protection["tp_plan"]),
                tp_order_ids=list(protection["tp_order_ids"]),
                tp_algo_ids=list(protection["tp_algo_ids"]),
                exchange_last_sync_at=int(trade.opened_at),
                exchange_last_sync_reason=str(protection["exchange_last_sync_reason"]),
                exchange_sync_status=str(protection["protection_status"]),
                exchange_sync_error=protection["protection_error"],
                exchange_position_amt=actual_position_qty,
                protection_status=str(protection["protection_status"]),
                protection_error=protection["protection_error"],
                protection_details=protection["protection_details"],
                exit_profile=exit_profile_to_dict(exit_profile),
                soft_stop_enabled=bool(exit_profile.soft_stop_enabled),
                soft_stop_activated_at=None,
                soft_stop_current_pct=None,
                soft_stop_last_raise_at=None,
                soft_stop_next_raise_at=None,
                soft_stop_trigger_price=None,
                exchange_safety_sl_price=initial_stop_price,
            )
        finally:
            await client.close()

    async def replace_stop_order(
        self,
        trade: Trade,
        *,
        new_stop_price: float,
        now_ms: int,
        current_open_orders: list[dict[str, Any]] | None = None,
    ) -> Trade:
        client, _, error = await self.account_provider.build_client(
            runtime_user_id=str(trade.user_id),
            require_active=True,
        )
        if client is None:
            raise RuntimeError(error or "live_api_unavailable")

        try:
            rules = await self._get_symbol_rules(client, trade.symbol)
            position_context = await self._resolve_position_context(client=client, trade=trade)
            desired_stop = self._round_stop_price(
                side=trade.side,
                raw_price=float(new_stop_price),
                tick_size=rules["tick_size"],
            )
            remaining_qty = self._round_qty_down(float(trade.qty_remaining or 0.0), rules["qty_step"])
            if remaining_qty <= 0:
                return trade

            lock_key = self._stop_replacement_lock_key(
                trade=trade,
                position_side=position_context["position_side"],
            )
            lock = self._stop_replacement_locks.setdefault(lock_key, asyncio.Lock())
            if lock.locked():
                log.info(
                    "[binance-live] stop replacement lock queued trade_id=%s symbol=%s positionSide=%s reason=%s",
                    trade.trade_id,
                    trade.symbol,
                    position_context["position_side"],
                    "replace_stop_order",
                )
            async with lock:
                log.info(
                    "[binance-live] stop replacement lock acquired trade_id=%s symbol=%s positionSide=%s reason=%s",
                    trade.trade_id,
                    trade.symbol,
                    position_context["position_side"],
                    "replace_stop_order",
                )
                return await self._replace_stop_order_locked(
                    client=client,
                    trade=trade,
                    rules=rules,
                    position_context=position_context,
                    desired_stop=desired_stop,
                    new_stop_price=float(new_stop_price),
                    remaining_qty=remaining_qty,
                    now_ms=now_ms,
                    current_open_orders=current_open_orders,
                )
        finally:
            await client.close()

    async def repair_exit_orders_for_remaining_position(
        self,
        trade: Trade,
        *,
        new_stop_price: float,
        now_ms: int,
        reason: str,
        current_open_orders: list[dict[str, Any]] | None = None,
    ) -> Trade:
        client, _, error = await self.account_provider.build_client(
            runtime_user_id=str(trade.user_id),
            require_active=True,
        )
        if client is None:
            raise RuntimeError(error or "live_api_unavailable")

        try:
            rules = await self._get_symbol_rules(client, trade.symbol)
            position_context = await self._resolve_position_context(client=client, trade=trade)
            position_match = await self._match_position_for_context(
                client=client,
                trade=trade,
                position_context=position_context,
            )
            if position_match.get("ambiguous") or position_match.get("confirmed_flat"):
                log.info(
                    "[binance-live] exit repair skipped trade_id=%s symbol=%s reason=%s ambiguous=%s confirmed_flat=%s",
                    trade.trade_id,
                    trade.symbol,
                    reason,
                    position_match.get("ambiguous"),
                    position_match.get("confirmed_flat"),
                )
                return trade

            remaining_qty = self._round_qty_down(abs(float(position_match.get("position_amt") or 0.0)), rules["qty_step"])
            if remaining_qty <= 0:
                return trade

            repaired = replace(
                trade,
                qty_remaining=remaining_qty,
                remaining_pct=(remaining_qty / trade.qty) if trade.qty > 0 else 0.0,
                exchange_position_amt=remaining_qty,
                exchange_position_mode=position_context["position_mode"],
                exchange_position_side=position_context["position_side"],
            )
            desired_stop = self._round_stop_price(
                side=trade.side,
                raw_price=float(new_stop_price),
                tick_size=rules["tick_size"],
            )
            lock_key = self._stop_replacement_lock_key(
                trade=repaired,
                position_side=position_context["position_side"],
            )
            lock = self._stop_replacement_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                repaired = await self._replace_stop_order_locked(
                    client=client,
                    trade=repaired,
                    rules=rules,
                    position_context=position_context,
                    desired_stop=desired_stop,
                    new_stop_price=float(new_stop_price),
                    remaining_qty=remaining_qty,
                    now_ms=now_ms,
                    current_open_orders=current_open_orders,
                )
                open_orders, open_algo_orders = await self._list_open_orders(client, symbol=trade.symbol)
                cancelled_tp_orders = await self._cancel_open_take_profit_algo_orders(
                    client=client,
                    trade=repaired,
                    position_context=position_context,
                    open_algo_orders=open_algo_orders,
                    reason=reason,
                )
                tp_orders, tp_failures = await self._create_repair_take_profit_orders(
                    client=client,
                    trade=repaired,
                    position_context=position_context,
                    rules=rules,
                    remaining_qty=remaining_qty,
                    reason=reason,
                )

            tp_plan = self._build_tp_plan(tp_orders)
            tp_order_ids = self._dedupe_order_ids(
                [self._order_identifier(order) for order in tp_orders]
            )
            tp_algo_ids = self._dedupe_order_ids(
                [self._string_or_none(order.get("algoId")) for order in tp_orders]
            )
            cancelled_ids = {
                item
                for item in (self._order_identifier(order) for order in cancelled_tp_orders)
                if item
            }
            order_ids = self._dedupe_order_ids(
                [
                    *[
                        order_id
                        for order_id in (repaired.exchange_order_ids or [])
                        if order_id not in cancelled_ids
                    ],
                    *tp_order_ids,
                ]
            )
            protection_status = self._resolve_protection_status(
                stop_order_present=repaired.exchange_stop_order is not None,
                tp_orders=tp_orders,
                tp_failures=tp_failures,
            )
            protection_error = self._build_protection_error_summary(
                protection_status=protection_status,
                stop_failure=None,
                tp_failures=tp_failures,
            )

            log.info(
                "[binance-live] repaired exit orders trade_id=%s symbol=%s reason=%s remaining_qty=%s cancelled_tp_orders=%s new_tp_orders=%s protection_status=%s",
                trade.trade_id,
                trade.symbol,
                reason,
                self._format_number(remaining_qty),
                json.dumps(self._summarize_open_orders(cancelled_tp_orders), separators=(",", ":")),
                json.dumps(self._summarize_open_orders(tp_orders), separators=(",", ":")),
                protection_status,
            )

            return replace(
                repaired,
                exchange_tp_orders=tp_orders,
                exchange_order_ids=order_ids,
                tp_count=max(int(getattr(repaired, "tp_count", 0) or 0), self._max_stage(tp_orders)),
                tp_levels=[item.get("level") for item in tp_plan],
                tp_close_fractions=[item.get("close_fraction") for item in tp_plan],
                tp_plan=tp_plan,
                tp_order_ids=tp_order_ids,
                tp_algo_ids=tp_algo_ids,
                exchange_last_sync_at=now_ms,
                exchange_last_sync_reason=f"exit_orders_repaired:{reason}",
                exchange_sync_status=protection_status,
                exchange_sync_error=protection_error,
                protection_status=protection_status,
                protection_error=protection_error,
                protection_details={
                    "reason": reason,
                    "remaining_qty": remaining_qty,
                    "cancelled_tp_order_count": len(cancelled_tp_orders),
                    "created_tp_order_count": len(tp_orders),
                    "tp_failures": tp_failures,
                    "position_match": position_match,
                },
            )
        finally:
            await client.close()

    async def _replace_stop_order_locked(
        self,
        *,
        client,
        trade: Trade,
        rules: dict[str, float],
        position_context: dict[str, str],
        desired_stop: float,
        new_stop_price: float,
        remaining_qty: float,
        now_ms: int,
        current_open_orders: list[dict[str, Any]] | None,
    ) -> Trade:
        current_stop = trade.exchange_stop_order or {}
        current_stop_id = self._string_or_none(current_stop.get("orderId"))
        current_stop_algo_id = self._string_or_none(current_stop.get("algoId"))
        current_stop_client_algo_id = self._string_or_none(
            current_stop.get("clientAlgoId") or current_stop.get("clientOrderId")
        )
        old_cancel_key = self._stop_cancel_key(current_stop_algo_id, current_stop_client_algo_id)
        cached_replacement = self._stop_replacement_cache.get(old_cancel_key) if old_cancel_key else None
        if cached_replacement:
            cached_stop = cached_replacement.get("stop_order") or {}
            cached_stop_price = self._to_float(cached_stop.get("stopPrice") or cached_stop.get("triggerPrice"))
            cached_stop_qty = self._to_float(cached_stop.get("origQty") or cached_stop.get("quantity"))
            if (
                abs(cached_stop_price - desired_stop) < max(rules["tick_size"], 1e-12)
                and abs(cached_stop_qty - remaining_qty) < max(rules["qty_step"], 1e-12)
            ):
                log.info(
                    "[binance-live] duplicate stop replacement ignored trade_id=%s symbol=%s old_algo_id=%s new_algo_id=%s new_client_algo_id=%s replacement_reason=%s cancel_result=%s",
                    trade.trade_id,
                    trade.symbol,
                    current_stop_algo_id,
                    self._string_or_none(cached_stop.get("algoId")),
                    self._string_or_none(cached_stop.get("clientAlgoId") or cached_stop.get("clientOrderId")),
                    "replace_stop_order",
                    cached_replacement.get("cancel_result"),
                )
                order_ids = self._dedupe_order_ids(
                    [
                        *self._filter_replaced_stop_order_ids(trade.exchange_order_ids or [], current_stop),
                        self._order_identifier(cached_stop),
                    ]
                )
                return replace(
                    trade,
                    sl_price=desired_stop,
                    exchange_position_mode=position_context["position_mode"],
                    exchange_position_side=position_context["position_side"],
                    exchange_stop_order=self._normalize_order_doc(cached_stop),
                    exchange_order_ids=order_ids,
                    exchange_last_sync_at=now_ms,
                    exchange_last_sync_reason="stop_replace_duplicate_ignored",
                    exchange_sync_status="ok",
                )

            current_stop = cached_stop
            current_stop_id = self._string_or_none(current_stop.get("orderId"))
            current_stop_algo_id = self._string_or_none(current_stop.get("algoId"))
            current_stop_client_algo_id = self._string_or_none(
                current_stop.get("clientAlgoId") or current_stop.get("clientOrderId")
            )

        current_stop_price = self._to_float(
            current_stop.get("stopPrice") or current_stop.get("triggerPrice")
        )
        current_stop_qty = self._to_float(current_stop.get("origQty") or current_stop.get("quantity"))
        if (
            (current_stop_algo_id or current_stop_client_algo_id)
            and abs(current_stop_price - desired_stop) < max(rules["tick_size"], 1e-12)
            and abs(current_stop_qty - remaining_qty) < max(rules["qty_step"], 1e-12)
        ):
            return replace(
                trade,
                exchange_stop_order=self._normalize_order_doc(current_stop),
                exchange_last_sync_at=now_ms,
                exchange_last_sync_reason="stop_unchanged",
                exchange_sync_status="ok",
            )

        current_stop_open = False
        if current_stop_id and not (current_stop_algo_id or current_stop_client_algo_id):
            active_orders = current_open_orders
            if active_orders is None:
                active_orders = await client.get_open_orders(symbol=trade.symbol) or []
            for order in active_orders:
                if self._string_or_none(order.get("orderId")) == current_stop_id:
                    current_stop_open = True
                    if (
                        abs(self._to_float(order.get("stopPrice") or order.get("triggerPrice")) - desired_stop)
                        < max(rules["tick_size"], 1e-12)
                        and abs(self._to_float(order.get("origQty") or order.get("quantity")) - remaining_qty)
                        < max(rules["qty_step"], 1e-12)
                    ):
                        return replace(
                            trade,
                            exchange_stop_order=self._normalize_order_doc(order),
                            exchange_last_sync_at=now_ms,
                            exchange_last_sync_reason="stop_unchanged",
                            exchange_sync_status="ok",
                        )
                    break

        stop_rounding_mode = ROUND_FLOOR if trade.side == Side.LONG else ROUND_CEILING
        rounded_stop_price = self._round_number(new_stop_price, rules["tick_size"], stop_rounding_mode)
        current_market_price = await self._current_price_for_stop_validation(
            client=client,
            trade=trade,
            position_context=position_context,
        )
        if self._stop_would_immediately_trigger(
            side=trade.side,
            stop_price=float(rounded_stop_price),
            current_price=current_market_price,
        ):
            log.warning(
                "[binance-live] STOP_SKIPPED_IMMEDIATE_TRIGGER trade_id=%s symbol=%s stop_price=%.8f current_price=%.8f positionSide=%s decision=skip_stop_replace",
                trade.trade_id,
                trade.symbol,
                float(rounded_stop_price),
                float(current_market_price),
                position_context["position_side"],
            )
            return replace(
                trade,
                sl_price=desired_stop,
                exchange_position_mode=position_context["position_mode"],
                exchange_position_side=position_context["position_side"],
                exchange_last_sync_at=now_ms,
                exchange_last_sync_reason="stop_skipped_immediate_trigger",
                exchange_sync_status="stop_skipped_immediate_trigger",
                protection_status="unprotected",
                protection_error="stop_would_immediately_trigger",
                protection_details={
                    **dict(getattr(trade, "protection_details", None) or {}),
                    "event_type": "STOP_SKIPPED_IMMEDIATE_TRIGGER",
                    "symbol": trade.symbol,
                    "trade_id": trade.trade_id,
                    "stop_price": float(rounded_stop_price),
                    "current_price": float(current_market_price),
                    "positionSide": position_context["position_side"],
                    "decision": "skip_stop_replace",
                },
            )

        cancel_result = "not_needed"
        cancel_key = self._stop_cancel_key(current_stop_algo_id, current_stop_client_algo_id)
        if current_stop_algo_id or current_stop_client_algo_id:
            if cancel_key and cancel_key in self._cancelled_stop_algo_keys:
                cancel_result = "already_cancelled_local"
            else:
                payload = await client.cancel_algo_order(
                    algo_id=current_stop_algo_id,
                    client_algo_id=None if current_stop_algo_id else current_stop_client_algo_id,
                )
                cancel_result = self._algo_cancel_status(payload)
                if cancel_key and cancel_result in {"cancelled", "already_missing"}:
                    self._cancelled_stop_algo_keys.add(cancel_key)
        elif current_stop_open and current_stop_id:
            await client.cancel_order(symbol=trade.symbol, order_id=current_stop_id)
            cancel_result = "cancelled"

        new_client_algo_id = self._build_client_order_id(f"s{trade.tp_hit_count}", trade.trade_id)
        new_stop_result = await self._create_algo_order_with_retry(
            client=client,
            symbol=trade.symbol.upper(),
            order_kind="stop_replace",
            base_params={
                "symbol": trade.symbol.upper(),
                "side": self._close_order_side(trade.side),
                "algoType": "CONDITIONAL",
                "type": "STOP_MARKET",
                **self._reduce_only_params(position_context["position_mode"]),
                "workingType": "MARK_PRICE",
                "priceProtect": "TRUE",
                "positionSide": position_context["position_side"],
                "clientAlgoId": new_client_algo_id,
                "newOrderRespType": "RESULT",
            },
            raw_qty=float(trade.qty_remaining or 0.0),
            qty_step=rules["qty_step"],
            raw_price=new_stop_price,
            tick_size=rules["tick_size"],
            price_rounding_mode=stop_rounding_mode,
        )
        new_stop = new_stop_result["order"]
        if new_stop is None:
            failure = new_stop_result["failure"] or {}
            if failure.get("exchange_error_code") == -2021:
                log.warning(
                    "[binance-live] STOP_SKIPPED_IMMEDIATE_TRIGGER trade_id=%s symbol=%s stop_price=%s current_price=%s exchange_error=%s decision=skip_stop_replace",
                    trade.trade_id,
                    trade.symbol,
                    failure.get("rounded_price") or failure.get("raw_price") or new_stop_price,
                    current_market_price,
                    failure.get("exchange_error_message") or failure.get("error"),
                )
                return replace(
                    trade,
                    sl_price=desired_stop,
                    exchange_position_mode=position_context["position_mode"],
                    exchange_position_side=position_context["position_side"],
                    exchange_last_sync_at=now_ms,
                    exchange_last_sync_reason="stop_skipped_immediate_trigger",
                    exchange_sync_status="stop_skipped_immediate_trigger",
                    protection_status="unprotected",
                    protection_error="stop_would_immediately_trigger",
                    protection_details={
                        **dict(getattr(trade, "protection_details", None) or {}),
                        "event_type": "STOP_SKIPPED_IMMEDIATE_TRIGGER",
                        "symbol": trade.symbol,
                        "trade_id": trade.trade_id,
                        "stop_price": failure.get("rounded_price") or failure.get("raw_price") or new_stop_price,
                        "current_price": current_market_price,
                        "positionSide": position_context["position_side"],
                        "exchange_error_code": failure.get("exchange_error_code"),
                        "exchange_error_message": failure.get("exchange_error_message"),
                        "decision": "skip_stop_replace",
                    },
                )
            raise RuntimeError(
                "stop_replace_failed:"
                f"{json.dumps(failure, separators=(',', ':'), default=str)}"
            )

        normalized_new_stop = self._normalize_order_doc(new_stop)
        if cancel_key and normalized_new_stop is not None:
            self._stop_replacement_cache[cancel_key] = {
                "stop_order": normalized_new_stop,
                "cancel_result": cancel_result,
            }

        log.info(
            "[binance-live] stop replaced trade_id=%s symbol=%s old_algo_id=%s new_client_algo_id=%s new_algo_id=%s replacement_reason=%s cancel_result=%s",
            trade.trade_id,
            trade.symbol,
            current_stop_algo_id,
            new_client_algo_id,
            self._string_or_none(new_stop.get("algoId")),
            "replace_stop_order",
            cancel_result,
        )

        order_ids = self._dedupe_order_ids(
            [
                *self._filter_replaced_stop_order_ids(trade.exchange_order_ids or [], current_stop),
                self._order_identifier(new_stop),
            ]
        )
        return replace(
            trade,
            sl_price=desired_stop,
            exchange_position_mode=position_context["position_mode"],
            exchange_position_side=position_context["position_side"],
            exchange_stop_order=normalized_new_stop,
            exchange_order_ids=order_ids,
            exchange_last_sync_at=now_ms,
            exchange_last_sync_reason="stop_replaced",
            exchange_sync_status="ok",
        )

    async def close_position_market(self, trade: Trade, *, now_ms: int, reason: str) -> Trade:
        client, _, error = await self.account_provider.build_client(
            runtime_user_id=str(trade.user_id),
            require_active=True,
        )
        if client is None:
            raise RuntimeError(error or "live_api_unavailable")

        try:
            rules = await self._get_symbol_rules(client, trade.symbol)
            position_context = await self._resolve_position_context(client=client, trade=trade)
            remaining_qty = self._round_qty_down(float(trade.qty_remaining or 0.0), rules["qty_step"])
            if remaining_qty <= 0:
                return trade

            close_client_order_id = self._build_client_order_id("mx", trade.trade_id)
            order = await client.create_order(
                symbol=trade.symbol.upper(),
                side=self._close_order_side(trade.side),
                type="MARKET",
                quantity=self._format_exchange_number(remaining_qty, rules["qty_step"]),
                **self._reduce_only_params(position_context["position_mode"]),
                positionSide=position_context["position_side"],
                newClientOrderId=close_client_order_id,
                newOrderRespType="RESULT",
            )
            self._log_precision_details(
                symbol=trade.symbol,
                order_kind="close_market",
                raw_qty=float(trade.qty_remaining or 0.0),
                rounded_qty=remaining_qty,
                step_size=rules["qty_step"],
            )
            await self._cleanup_exit_orders_after_close_if_flat(
                client=client,
                trade=trade,
                position_context=position_context,
                reason=f"post_close_cleanup:{reason}",
            )
            order_ids = self._dedupe_order_ids(
                [*list(trade.exchange_order_ids or []), self._string_or_none(order.get("orderId"))]
            )
            return replace(
                trade,
                exchange_position_mode=position_context["position_mode"],
                exchange_position_side=position_context["position_side"],
                exchange_order_ids=order_ids,
                exchange_close_order_id=self._string_or_none(order.get("orderId")),
                exchange_close_client_order_id=self._string_or_none(order.get("clientOrderId") or close_client_order_id),
                exchange_last_sync_at=now_ms,
                exchange_last_sync_reason=reason,
                exchange_sync_status="manual_close_sent",
            )
        finally:
            await client.close()

    async def _resolve_position_context(self, *, client, trade: Trade) -> dict[str, str]:
        mode_payload = await client.get_position_mode()
        position_mode = "hedge" if self._is_hedge_position_mode(mode_payload) else "one_way"
        position_side = self._position_side_for_trade(trade=trade, position_mode=position_mode)
        self._validate_position_side(position_mode=position_mode, position_side=position_side)

        log.info(
            "[binance-live] position context user=%s symbol=%s mode=%s positionSide=%s",
            trade.user_id,
            trade.symbol,
            position_mode,
            position_side,
        )
        return {
            "position_mode": position_mode,
            "position_side": position_side,
        }

    async def _ensure_terminal_entry_status(self, client, *, trade: Trade, entry_order: dict[str, Any]) -> dict[str, Any]:
        status = str(entry_order.get("status") or "")
        if status in {"FILLED", "PARTIALLY_FILLED"}:
            return entry_order

        order_id = self._string_or_none(entry_order.get("orderId"))
        if not order_id:
            return entry_order

        current = entry_order
        for _ in range(self.config.order_status_poll_attempts):
            await asyncio.sleep(self.config.order_status_poll_delay_seconds)
            fetched = await client.get_order(symbol=trade.symbol, order_id=order_id)
            if not fetched:
                continue
            current = fetched
            status = str(current.get("status") or "")
            if status in {"FILLED", "PARTIALLY_FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                break
        return current

    async def _get_symbol_rules(self, client, symbol: str) -> dict[str, float]:
        symbol_upper = symbol.upper()
        cached = self._symbol_rules_cache.get(symbol_upper)
        if cached is not None:
            return cached

        exchange_info = await client.get_exchange_info()
        if not exchange_info:
            rules = {"tick_size": 0.1, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 0.0}
            self._symbol_rules_cache[symbol_upper] = rules
            return rules

        for row in exchange_info.get("symbols", []):
            if str(row.get("symbol", "")).upper() != symbol_upper:
                continue
            tick_size = 0.1
            qty_step = 0.001
            min_qty = 0.001
            min_notional = 0.0
            for item in row.get("filters", []):
                filter_type = str(item.get("filterType") or "")
                if filter_type == "PRICE_FILTER":
                    tick_size = self._to_float(item.get("tickSize"), default=tick_size)
                if filter_type in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
                    qty_step = self._to_float(item.get("stepSize"), default=qty_step)
                    min_qty = self._to_float(item.get("minQty"), default=min_qty)
                if filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
                    min_notional = self._to_float(
                        item.get("notional") or item.get("minNotional"),
                        default=min_notional,
                    )
            rules = {
                "tick_size": max(float(tick_size), 1e-12),
                "qty_step": max(float(qty_step), 1e-12),
                "min_qty": max(float(min_qty), 0.0),
                "min_notional": max(float(min_notional), 0.0),
            }
            self._symbol_rules_cache[symbol_upper] = rules
            return rules

        rules = {"tick_size": 0.1, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 0.0}
        self._symbol_rules_cache[symbol_upper] = rules
        return rules

    async def _finalize_entry_submission(
        self,
        *,
        client,
        trade: Trade,
        rules: dict[str, float],
        raw_qty: float,
        latest_entry_price: float,
    ) -> dict[str, Any]:
        qty_step = float(rules["qty_step"])
        min_qty = float(rules["min_qty"])
        min_notional = float(rules["min_notional"])
        leverage = max(int(trade.leverage or 1), 1)
        min_notional_buffer_pct = self._min_notional_buffer_pct()
        target_notional = self._market_entry_target_notional(min_notional)

        rounded_qty_before_bump = self._round_qty_down(raw_qty, qty_step)
        rounded_qty_after_bump = rounded_qty_before_bump
        if min_qty > 0 and rounded_qty_after_bump > 0 and rounded_qty_after_bump < min_qty:
            rounded_qty_after_bump = self._ceil_to_step(min_qty, qty_step)
        if target_notional > 0 and rounded_qty_after_bump > 0:
            current_notional = rounded_qty_after_bump * latest_entry_price
            if current_notional < target_notional:
                min_qty_for_notional = self._ceil_to_step(target_notional / latest_entry_price, qty_step)
                rounded_qty_after_bump = max(rounded_qty_after_bump, min_qty_for_notional)
                rounded_qty_after_bump = self._ceil_to_step(rounded_qty_after_bump, qty_step)

        planned_notional = rounded_qty_before_bump * latest_entry_price
        adjusted_notional = rounded_qty_after_bump * latest_entry_price
        required_margin_before = planned_notional / leverage if leverage > 0 else planned_notional
        required_margin_after = adjusted_notional / leverage if leverage > 0 else adjusted_notional
        bump_applied = rounded_qty_after_bump > rounded_qty_before_bump

        margin_snapshot = await self._entry_margin_snapshot(
            client=client,
            required_margin_after=required_margin_after,
            load_snapshot=bump_applied,
        )
        diagnostics = {
            "raw_qty": float(raw_qty),
            "rounded_qty_before_bump": float(rounded_qty_before_bump),
            "rounded_qty_after_bump": float(rounded_qty_after_bump),
            "entry_price_used_for_notional": float(latest_entry_price),
            "price_for_notional": float(latest_entry_price),
            "planned_notional": float(planned_notional),
            "adjusted_notional": float(adjusted_notional),
            "min_notional": float(min_notional),
            "min_notional_buffer_pct": float(min_notional_buffer_pct),
            "target_notional": float(target_notional),
            "stepSize": float(qty_step),
            "leverage": int(leverage),
            "required_margin_before": float(required_margin_before),
            "required_margin_after": float(required_margin_after),
            "required_margin_after_buffer": float(required_margin_after),
            "margin_after": margin_snapshot["margin_after"],
            "margin_limit": margin_snapshot["margin_limit"],
            "available_balance": margin_snapshot["available_balance"],
            "bump_applied": bool(bump_applied),
        }

        self._log_entry_notional_diagnostics(
            symbol=trade.symbol,
            trade_id=trade.trade_id,
            diagnostics=diagnostics,
        )

        if bump_applied:
            if (
                margin_snapshot["available_balance"] is not None
                and required_margin_after > float(margin_snapshot["available_balance"])
            ):
                raise RuntimeError(self._build_entry_guard_error("entry_bump_blocked_by_available_balance", diagnostics))
            if (
                margin_snapshot["margin_after"] is not None
                and margin_snapshot["margin_limit"] is not None
                and float(margin_snapshot["margin_after"]) > float(margin_snapshot["margin_limit"])
            ):
                raise RuntimeError(self._build_entry_guard_error("entry_bump_blocked_by_global_margin_limit", diagnostics))

        return diagnostics

    async def _ensure_entry_leverage(self, *, client, trade: Trade) -> None:
        desired_leverage = max(int(trade.leverage or 1), 1)
        try:
            await self._apply_leverage(client=client, symbol=trade.symbol, leverage=desired_leverage)
            return
        except Exception as exc:
            log.warning(
                "[binance-live] set leverage failed, retrying once trade_id=%s symbol=%s leverage=%s error=%s:%s",
                trade.trade_id,
                trade.symbol,
                desired_leverage,
                type(exc).__name__,
                exc,
            )
        await self._apply_leverage(client=client, symbol=trade.symbol, leverage=desired_leverage)

    async def _apply_leverage(self, *, client, symbol: str, leverage: int):
        if hasattr(client, "ensure_leverage"):
            return await client.ensure_leverage(symbol=symbol, leverage=int(leverage))
        return await client.change_leverage(symbol=symbol, leverage=int(leverage))

    async def _entry_margin_snapshot(
        self,
        *,
        client,
        required_margin_after: float,
        load_snapshot: bool,
    ) -> dict[str, float | None]:
        if not load_snapshot or not hasattr(client, "get_account_info"):
            return {
                "available_balance": None,
                "margin_current_used": None,
                "margin_after": None,
                "margin_limit": None,
            }

        try:
            account_info = await client.get_account_info()
        except Exception as exc:
            log.warning(
                "[binance-live] failed to load account snapshot for final entry guard error=%s:%s",
                type(exc).__name__,
                exc,
            )
            return {
                "available_balance": None,
                "margin_current_used": None,
                "margin_after": None,
                "margin_limit": None,
            }

        if not isinstance(account_info, dict):
            return {
                "available_balance": None,
                "margin_current_used": None,
                "margin_after": None,
                "margin_limit": None,
            }

        available_balance = self._to_float(
            account_info.get("availableBalance")
            or account_info.get("available_balance")
            or account_info.get("available_balance_usdt"),
            default=None,
        )
        margin_current_used = self._to_float(
            account_info.get("totalInitialMargin")
            or account_info.get("total_initial_margin")
            or account_info.get("totalPositionInitialMargin")
            or account_info.get("total_position_initial_margin"),
            default=None,
        )
        margin_limit = self._to_float(
            account_info.get("totalMarginBalance")
            or account_info.get("total_margin_balance"),
            default=None,
        )
        margin_after = (
            float(margin_current_used) + float(required_margin_after)
            if margin_current_used is not None
            else None
        )
        return {
            "available_balance": available_balance,
            "margin_current_used": margin_current_used,
            "margin_after": margin_after,
            "margin_limit": margin_limit,
        }

    def _build_entry_guard_error(self, reason: str, diagnostics: dict[str, Any]) -> str:
        parts = [f"reason={reason}"]
        for key in (
            "raw_qty",
            "rounded_qty_before_bump",
            "rounded_qty_after_bump",
            "entry_price_used_for_notional",
            "price_for_notional",
            "planned_notional",
            "adjusted_notional",
            "min_notional",
            "min_notional_buffer_pct",
            "target_notional",
            "stepSize",
            "leverage",
            "required_margin_before",
            "required_margin_after",
            "required_margin_after_buffer",
            "margin_after",
            "margin_limit",
            "available_balance",
            "bump_applied",
        ):
            value = diagnostics.get(key)
            if value is None:
                continue
            parts.append(f"{key}={value}")
        return "entry_final_guard_blocked:" + ";".join(parts)

    def _log_entry_notional_diagnostics(self, *, symbol: str, trade_id: str, diagnostics: dict[str, Any]) -> None:
        log.info(
            "[binance-live] entry final guard trade_id=%s symbol=%s raw_qty=%s rounded_qty_before_bump=%s rounded_qty_after_bump=%s entry_price_used_for_notional=%s price_for_notional=%s planned_notional=%s adjusted_notional=%s min_notional=%s min_notional_buffer_pct=%s target_notional=%s stepSize=%s leverage=%s required_margin_before=%s required_margin_after=%s required_margin_after_buffer=%s margin_after=%s margin_limit=%s available_balance=%s bump_applied=%s",
            trade_id,
            symbol,
            diagnostics.get("raw_qty"),
            diagnostics.get("rounded_qty_before_bump"),
            diagnostics.get("rounded_qty_after_bump"),
            diagnostics.get("entry_price_used_for_notional"),
            diagnostics.get("price_for_notional"),
            diagnostics.get("planned_notional"),
            diagnostics.get("adjusted_notional"),
            diagnostics.get("min_notional"),
            diagnostics.get("min_notional_buffer_pct"),
            diagnostics.get("target_notional"),
            diagnostics.get("stepSize"),
            diagnostics.get("leverage"),
            diagnostics.get("required_margin_before"),
            diagnostics.get("required_margin_after"),
            diagnostics.get("required_margin_after_buffer"),
            diagnostics.get("margin_after"),
            diagnostics.get("margin_limit"),
            diagnostics.get("available_balance"),
            diagnostics.get("bump_applied"),
        )

    def _min_notional_buffer_pct(self) -> float:
        try:
            return max(float(self.config.min_notional_buffer_pct), 0.0)
        except (TypeError, ValueError):
            return 0.02

    def _market_entry_target_notional(self, min_notional: float) -> float:
        min_notional_value = max(float(min_notional or 0.0), 0.0)
        if min_notional_value <= 0:
            return 0.0
        return min_notional_value * (1.0 + self._min_notional_buffer_pct())

    async def _resolve_actual_position_qty(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
        executed_qty: float,
        qty_step: float,
    ) -> float:
        actual_qty = self._round_qty_down(executed_qty, qty_step)
        try:
            position_match = await self._match_position_for_context(
                client=client,
                trade=trade,
                position_context=position_context,
            )
        except Exception as exc:
            log.warning(
                "[binance-live] failed to resolve actual position qty trade_id=%s symbol=%s error=%s:%s",
                trade.trade_id,
                trade.symbol,
                type(exc).__name__,
                exc,
            )
            return actual_qty

        matched_qty = self._round_qty_down(abs(float(position_match.get("position_amt") or 0.0)), qty_step)
        if not position_match.get("ambiguous") and matched_qty > 0:
            return matched_qty
        return actual_qty

    async def _setup_entry_protection(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
        rules: dict[str, float],
        actual_position_qty: float,
        entry_price: float,
        initial_stop_raw_price: float,
        initial_stop_price: float,
        entry_order_id: str | None,
    ) -> dict[str, Any]:
        tp_orders: list[dict[str, Any]] = []
        tp_failures: list[dict[str, Any]] = []
        order_ids: list[str] = []

        stop_result = await self._create_algo_order_with_retry(
            client=client,
            symbol=trade.symbol.upper(),
            order_kind="stop_initial",
            base_params={
                "symbol": trade.symbol.upper(),
                "side": self._close_order_side(trade.side),
                "algoType": "CONDITIONAL",
                "type": "STOP_MARKET",
                **self._reduce_only_params(position_context["position_mode"]),
                "workingType": "MARK_PRICE",
                "priceProtect": "TRUE",
                "positionSide": position_context["position_side"],
                "clientAlgoId": self._build_client_order_id("s0", trade.trade_id),
                "newOrderRespType": "RESULT",
            },
            raw_qty=actual_position_qty,
            qty_step=rules["qty_step"],
            raw_price=initial_stop_raw_price,
            tick_size=rules["tick_size"],
            price_rounding_mode=ROUND_FLOOR if trade.side == Side.LONG else ROUND_CEILING,
        )

        stop_order = stop_result["order"]
        stop_request = stop_result["request"]
        stop_failure = stop_result["failure"]
        normalized_stop_order = self._normalize_order_doc(stop_order) if stop_order else None
        if stop_order:
            order_ids.append(self._order_identifier(stop_order))

        tp_specs = self._build_tp_specs(
            trade=trade,
            entry_price=entry_price,
            qty=actual_position_qty,
            tick_size=rules["tick_size"],
            qty_step=rules["qty_step"],
        )

        if stop_order:
            for tp_spec in tp_specs:
                if tp_spec["qty"] <= 0:
                    continue
                tp_result = await self._create_algo_order_with_retry(
                    client=client,
                    symbol=trade.symbol.upper(),
                    order_kind=f"tp_stage_{tp_spec['stage']}",
                    base_params={
                        "symbol": trade.symbol.upper(),
                        "side": self._close_order_side(trade.side),
                        "algoType": "CONDITIONAL",
                        "type": "TAKE_PROFIT_MARKET",
                        **self._reduce_only_params(position_context["position_mode"]),
                        "workingType": "MARK_PRICE",
                        "priceProtect": "TRUE",
                        "positionSide": position_context["position_side"],
                        "clientAlgoId": str(tp_spec["client_order_id"]),
                        "newOrderRespType": "RESULT",
                    },
                    raw_qty=float(tp_spec["raw_qty"]),
                    qty_step=rules["qty_step"],
                    raw_price=float(tp_spec["raw_price"]),
                    tick_size=rules["tick_size"],
                    price_rounding_mode=ROUND_CEILING if trade.side == Side.LONG else ROUND_FLOOR,
                )
                tp_order = tp_result["order"]
                if tp_order is None:
                    failure = dict(tp_result["failure"] or {})
                    failure["stage"] = int(tp_spec["stage"])
                    tp_failures.append(failure)
                    continue

                tp_order["stage"] = int(tp_spec["stage"])
                tp_order["tp_count"] = int(tp_spec["tp_count"])
                tp_order["level"] = float(tp_spec["level"])
                tp_order["close_fraction"] = float(tp_spec["close_fraction"])
                tp_order["close_pct"] = float(tp_spec["close_fraction"]) * 100.0
                tp_order["target_price"] = float(tp_result["request"].get("rounded_price") or tp_spec["price"])
                tp_order["target_qty"] = float(tp_result["request"].get("rounded_qty") or tp_spec["qty"])
                tp_order["target_notional"] = float(tp_order["target_qty"]) * float(entry_price)
                normalized = self._normalize_order_doc(tp_order)
                tp_orders.append(normalized if normalized is not None else dict(tp_order))
                order_ids.append(self._order_identifier(tp_order))

        tp_plan = self._build_tp_plan(tp_orders)
        tp_order_ids = self._dedupe_order_ids(
            [self._order_identifier(order) for order in tp_orders]
        )
        tp_algo_ids = self._dedupe_order_ids(
            [self._string_or_none(order.get("algoId")) for order in tp_orders]
        )

        protection_status = self._resolve_protection_status(
            stop_order_present=normalized_stop_order is not None,
            tp_orders=tp_orders,
            tp_failures=tp_failures,
        )
        first_failure = stop_failure or (tp_failures[0] if tp_failures else None)
        protection_error = self._build_protection_error_summary(
            protection_status=protection_status,
            stop_failure=stop_failure,
            tp_failures=tp_failures,
        )
        protection_details = {
            "symbol": trade.symbol,
            "trade_id": trade.trade_id,
            "strategy_version": getattr(trade, "strategy_version", "v1"),
            "exit_profile": exit_profile_to_dict(self._exit_profile(trade)),
            "entry_order_id": entry_order_id,
            "actual_position_amt": actual_position_qty,
            "protection_status": protection_status,
            "attempted_tp_qty": float(sum(float(spec.get("qty") or 0.0) for spec in tp_specs)),
            "attempted_sl_qty": float(stop_request.get("rounded_qty") or 0.0),
            "raw_qty": first_failure.get("raw_qty") if first_failure else stop_request.get("raw_qty"),
            "rounded_qty": first_failure.get("rounded_qty") if first_failure else stop_request.get("rounded_qty"),
            "raw_price": first_failure.get("raw_price") if first_failure else None,
            "rounded_price": first_failure.get("rounded_price") if first_failure else None,
            "raw_stop_price": stop_request.get("raw_price"),
            "rounded_stop_price": stop_request.get("rounded_price"),
            "stepSize": rules["qty_step"],
            "tickSize": rules["tick_size"],
            "tp_order_count_requested": len([spec for spec in tp_specs if spec["qty"] > 0]),
            "tp_order_count_created": len(tp_orders),
            "tp_count": len(tp_orders),
            "tp_count_original": tp_specs[0].get("original_tp_count") if tp_specs else None,
            "tp_count_forced_by_small_margin": bool(
                tp_specs and tp_specs[0].get("tp_count_forced_by_small_margin")
            ),
            "tp_count_force_reason": tp_specs[0].get("tp_count_force_reason") if tp_specs else None,
            "entry_margin_usdt": tp_specs[0].get("entry_margin_usdt") if tp_specs else None,
            "position_notional": tp_specs[0].get("position_notional") if tp_specs else None,
            "tp_levels": [item.get("level") for item in tp_plan],
            "tp_close_fractions": [item.get("close_fraction") for item in tp_plan],
            "tp_plan": tp_plan,
            "tp_order_ids": tp_order_ids,
            "tp_algo_ids": tp_algo_ids,
            "stop_created": normalized_stop_order is not None,
            "tp_failures": tp_failures,
            "stop_failure": stop_failure,
            "exchange_error_code": first_failure.get("exchange_error_code") if first_failure else None,
            "exchange_error_message": first_failure.get("exchange_error_message") if first_failure else None,
        }

        if protection_status != "protected":
            log.error(
                "[binance-live] protection setup incomplete trade_id=%s symbol=%s status=%s details=%s",
                trade.trade_id,
                trade.symbol,
                protection_status,
                json.dumps(protection_details, separators=(",", ":"), default=str),
            )

        return {
            "tp_orders": tp_orders,
            "stop_order": normalized_stop_order,
            "order_ids": self._dedupe_order_ids(order_ids),
            "tp_count": len(tp_orders),
            "tp_levels": [item.get("level") for item in tp_plan],
            "tp_close_fractions": [item.get("close_fraction") for item in tp_plan],
            "tp_plan": tp_plan,
            "tp_order_ids": tp_order_ids,
            "tp_algo_ids": tp_algo_ids,
            "protection_status": protection_status,
            "protection_error": protection_error,
            "protection_details": protection_details,
            "exchange_last_sync_reason": "entry_opened" if protection_status == "protected" else "protection_setup_failed",
        }

    async def _cancel_open_take_profit_algo_orders(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
        open_algo_orders: list[dict[str, Any]],
        reason: str,
    ) -> list[dict[str, Any]]:
        cancelled: list[dict[str, Any]] = []
        expected_side = str(position_context["position_side"]).upper()
        for order in open_algo_orders:
            if not self._is_open_take_profit_algo_order(order):
                continue
            if not self._position_side_matches(order, expected_side):
                continue
            algo_id = self._string_or_none(order.get("algoId"))
            client_algo_id = self._string_or_none(order.get("clientAlgoId") or order.get("clientOrderId"))
            if not (algo_id or client_algo_id):
                continue
            payload = await client.cancel_algo_order(
                algo_id=algo_id,
                client_algo_id=None if algo_id else client_algo_id,
            )
            normalized = self._normalize_order_doc(order) or dict(order)
            normalized["cancel_result"] = self._algo_cancel_status(payload)
            cancelled.append(normalized)
            log.info(
                "[binance-live] cancelled stale TP during repair trade_id=%s symbol=%s reason=%s algo_id=%s client_algo_id=%s result=%s",
                trade.trade_id,
                trade.symbol,
                reason,
                algo_id,
                client_algo_id,
                normalized["cancel_result"],
            )
        return cancelled

    async def _create_repair_take_profit_orders(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
        rules: dict[str, float],
        remaining_qty: float,
        reason: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        spec = self._build_repair_tp_spec(
            trade=trade,
            remaining_qty=remaining_qty,
            tick_size=rules["tick_size"],
            qty_step=rules["qty_step"],
        )
        if spec is None:
            return [], []

        result = await self._create_algo_order_with_retry(
            client=client,
            symbol=trade.symbol.upper(),
            order_kind=f"tp_repair_stage_{spec['stage']}",
            base_params={
                "symbol": trade.symbol.upper(),
                "side": self._close_order_side(trade.side),
                "algoType": "CONDITIONAL",
                "type": "TAKE_PROFIT_MARKET",
                **self._reduce_only_params(position_context["position_mode"]),
                "workingType": "MARK_PRICE",
                "priceProtect": "TRUE",
                "positionSide": position_context["position_side"],
                "clientAlgoId": str(spec["client_order_id"]),
                "newOrderRespType": "RESULT",
            },
            raw_qty=float(spec["raw_qty"]),
            qty_step=rules["qty_step"],
            raw_price=float(spec["raw_price"]),
            tick_size=rules["tick_size"],
            price_rounding_mode=ROUND_CEILING if trade.side == Side.LONG else ROUND_FLOOR,
        )
        order = result["order"]
        if order is None:
            failure = dict(result["failure"] or {})
            failure["stage"] = int(spec["stage"])
            failure["repair_reason"] = reason
            return [], [failure]

        order["stage"] = int(spec["stage"])
        order["tp_count"] = int(spec["tp_count"])
        order["level"] = float(spec["level"])
        order["close_fraction"] = float(spec["close_fraction"])
        order["close_pct"] = float(spec["close_fraction"]) * 100.0
        order["target_price"] = float(result["request"].get("rounded_price") or spec["price"])
        order["target_qty"] = float(result["request"].get("rounded_qty") or spec["qty"])
        order["target_notional"] = float(order["target_qty"]) * float(trade.entry_price)
        normalized = self._normalize_order_doc(order)
        return [normalized if normalized is not None else dict(order)], []

    def _build_repair_tp_spec(
        self,
        *,
        trade: Trade,
        remaining_qty: float,
        tick_size: float,
        qty_step: float,
    ) -> dict[str, Any] | None:
        stage = self._next_repair_tp_stage(trade)
        if stage is None:
            return None
        level = self._tp_level_for_stage(stage, trade=trade)
        if level is None:
            return None
        tp_qty = self._round_qty_down(remaining_qty, qty_step)
        if tp_qty <= 0:
            return None
        raw_price, target_price = self._compute_tp_price_parts(
            side=trade.side,
            entry_price=float(trade.entry_price),
            level=level,
            tick_size=tick_size,
        )
        return {
            "stage": stage,
            "tp_count": max(int(getattr(trade, "tp_count", 0) or 0), stage),
            "level": level,
            "price": target_price,
            "qty": tp_qty,
            "raw_qty": remaining_qty,
            "raw_price": raw_price,
            "close_fraction": (tp_qty / trade.qty) if trade.qty > 0 else 0.0,
            "client_order_id": self._build_client_order_id(f"rt{stage}", trade.trade_id),
        }

    def _next_repair_tp_stage(self, trade: Trade) -> int | None:
        next_stage = int(getattr(trade, "tp_hit_count", 0) or 0) + 1
        max_stage = min(int(self._exit_profile(trade).max_tp_count), int(self.config.max_tp_levels), MAX_TP_LEVELS)
        if next_stage > max_stage:
            return None
        return max(next_stage, 1)

    def _tp_level_for_stage(self, stage: int, *, trade: Trade | None = None) -> float | None:
        levels = self._active_tp_levels(trade)
        if not levels:
            return None
        index = min(max(int(stage), 1), len(levels)) - 1
        return float(levels[index])

    def _max_stage(self, orders: list[dict[str, Any]]) -> int:
        stages: list[int] = []
        for order in orders:
            try:
                stage = int(order.get("stage") or 0)
            except (TypeError, ValueError):
                stage = 0
            if stage > 0:
                stages.append(stage)
        return max(stages) if stages else 0

    def _is_open_take_profit_algo_order(self, order: dict[str, Any]) -> bool:
        order_type = str(order.get("type") or order.get("orderType") or "").upper()
        status = str(order.get("status") or order.get("algoStatus") or "").upper()
        return "TAKE_PROFIT" in order_type and status in {"NEW", "PARTIALLY_FILLED"}

    def _resolve_protection_status(
        self,
        *,
        stop_order_present: bool,
        tp_orders: list[dict[str, Any]],
        tp_failures: list[dict[str, Any]],
    ) -> str:
        if not stop_order_present:
            return "unprotected"
        if tp_failures or not tp_orders:
            return "partially_protected"
        return "protected"

    def _build_protection_error_summary(
        self,
        *,
        protection_status: str,
        stop_failure: dict[str, Any] | None,
        tp_failures: list[dict[str, Any]],
    ) -> str | None:
        if protection_status == "protected":
            return None
        if stop_failure:
            message = stop_failure.get("exchange_error_message") or stop_failure.get("error") or "stop_creation_failed"
            return f"stop_creation_failed:{message}"
        if tp_failures:
            message = tp_failures[0].get("exchange_error_message") or tp_failures[0].get("error") or "tp_creation_failed"
            return f"tp_creation_failed:{message}"
        return "protection_incomplete"

    async def _create_algo_order_with_retry(
        self,
        *,
        client,
        symbol: str,
        order_kind: str,
        base_params: dict[str, Any],
        raw_qty: float,
        qty_step: float,
        raw_price: float | None = None,
        tick_size: float | None = None,
        price_rounding_mode: str | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            request = self._build_algo_order_request(
                base_params=base_params,
                raw_qty=raw_qty,
                qty_step=qty_step,
                raw_price=raw_price,
                tick_size=tick_size,
                price_rounding_mode=price_rounding_mode,
            )
            self._log_precision_details(
                symbol=symbol,
                order_kind=order_kind if attempt == 0 else f"{order_kind}_retry",
                raw_qty=request.get("raw_qty"),
                rounded_qty=request.get("rounded_qty"),
                step_size=qty_step,
                raw_price=request.get("raw_price"),
                rounded_price=request.get("rounded_price"),
                tick_size=tick_size,
            )
            try:
                order = await client.create_algo_order(**request["params"])
                return {
                    "order": order,
                    "request": request,
                    "failure": None,
                }
            except Exception as exc:
                failure = self._build_algo_order_failure_payload(
                    exc=exc,
                    symbol=symbol,
                    order_kind=order_kind,
                    request=request,
                    qty_step=qty_step,
                    tick_size=tick_size,
                    attempt=attempt + 1,
                )
                if attempt == 0 and failure.get("exchange_error_code") == -1111:
                    log.warning(
                        "[binance-live] retrying algo order after precision error symbol=%s order_kind=%s params=%s error_code=%s error_message=%s",
                        symbol,
                        order_kind,
                        json.dumps(request["params"], separators=(",", ":"), default=str),
                        failure.get("exchange_error_code"),
                        failure.get("exchange_error_message"),
                    )
                    continue
                return {
                    "order": None,
                    "request": request,
                    "failure": failure,
                }

        return {
            "order": None,
            "request": request,
            "failure": {
                "symbol": symbol,
                "order_kind": order_kind,
                "error": "unknown_algo_order_failure",
            },
        }

    def _build_algo_order_request(
        self,
        *,
        base_params: dict[str, Any],
        raw_qty: float,
        qty_step: float,
        raw_price: float | None = None,
        tick_size: float | None = None,
        price_rounding_mode: str | None = None,
        price_field: str = "triggerPrice",
    ) -> dict[str, Any]:
        rounded_qty = self._round_number(raw_qty, qty_step, ROUND_DOWN)
        params = dict(base_params)
        params["quantity"] = self._format_exchange_number(rounded_qty, qty_step)

        rounded_price = None
        if raw_price is not None and tick_size is not None:
            rounding = price_rounding_mode or ROUND_HALF_UP
            rounded_price = self._round_number(raw_price, tick_size, rounding)
            if price_field:
                params[price_field] = self._format_exchange_number(rounded_price, tick_size)
            for field in ("price", "stopPrice", "triggerPrice", "activationPrice"):
                if field in params and field != price_field:
                    params[field] = self._format_exchange_number(rounded_price, tick_size)

        if "callbackRate" in params and params["callbackRate"] is not None:
            callback_rate = self._round_number(float(params["callbackRate"]), 0.1, ROUND_DOWN)
            params["callbackRate"] = self._format_exchange_number(callback_rate, 0.1)

        return {
            "params": params,
            "raw_qty": float(raw_qty),
            "rounded_qty": float(rounded_qty),
            "raw_price": float(raw_price) if raw_price is not None else None,
            "rounded_price": float(rounded_price) if rounded_price is not None else None,
        }

    def _build_algo_order_failure_payload(
        self,
        *,
        exc: Exception,
        symbol: str,
        order_kind: str,
        request: dict[str, Any],
        qty_step: float,
        tick_size: float | None,
        attempt: int,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "order_kind": order_kind,
            "attempt": int(attempt),
            "raw_qty": request.get("raw_qty"),
            "rounded_qty": request.get("rounded_qty"),
            "raw_price": request.get("raw_price"),
            "rounded_price": request.get("rounded_price"),
            "stepSize": qty_step,
            "tickSize": tick_size,
            "params": dict(request.get("params") or {}),
        }
        payload.update(self._http_exception_details(exc))
        log.error(
            "[binance-live] algo order failed symbol=%s order_kind=%s attempt=%s params=%s payload=%s",
            symbol,
            order_kind,
            attempt,
            json.dumps(payload.get("params"), separators=(",", ":"), default=str),
            json.dumps(payload, separators=(",", ":"), default=str),
        )
        return payload

    def _http_exception_details(self, exc: Exception) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason": type(exc).__name__,
            "error": self._sanitize_error_text(str(exc)),
        }
        if not isinstance(exc, httpx.HTTPStatusError):
            return payload

        response = exc.response
        if response is None:
            return payload

        payload["http_status"] = getattr(response, "status_code", None)
        payload["http_reason"] = str(getattr(response, "reason_phrase", "") or "").strip() or None
        request = getattr(response, "request", None)
        url = getattr(request, "url", None) if request is not None else None
        if url is not None:
            payload["http_path"] = str(getattr(url, "path", "") or str(url).split("?", 1)[0])

        response_payload = self._response_payload(response)
        if isinstance(response_payload, dict):
            payload["exchange_error_code"] = response_payload.get("code")
            if response_payload.get("msg") is not None:
                payload["exchange_error_message"] = self._sanitize_error_text(str(response_payload.get("msg")))
        elif response_payload:
            payload["exchange_error_text"] = self._sanitize_error_text(str(response_payload))
        return payload

    def _http_exception_response_body(self, exc: Exception) -> str | None:
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        response = exc.response
        if response is None:
            return None
        try:
            return self._sanitize_error_text(str(response.text or ""))
        except Exception:
            payload = self._response_payload(response)
            return self._sanitize_error_text(str(payload)) if payload is not None else None

    def _response_payload(self, response: httpx.Response) -> dict[str, Any] | str | None:
        try:
            payload = response.json()
        except Exception:
            try:
                return self._truncate(str(response.text or ""))
            except Exception:
                return None
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return self._truncate(str(payload))
        return None

    def _sanitize_error_text(self, text: str) -> str:
        return self._truncate(str(text))

    def _truncate(self, text: str, limit: int = 500) -> str:
        text = str(text)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    async def _cleanup_stale_exit_orders_before_entry(self, *, client, trade: Trade, position_context: dict[str, str]) -> None:
        open_orders, open_algo_orders = await self._list_open_orders(client, symbol=trade.symbol)
        summaries = self._summarize_open_orders([*open_orders, *open_algo_orders])
        if not summaries:
            return

        position_match = await self._match_position_for_context(
            client=client,
            trade=trade,
            position_context=position_context,
        )
        if position_match["ambiguous"]:
            raise RuntimeError(
                "pre_entry_cleanup_ambiguous_position:"
                f"trade_id={trade.trade_id};"
                f"symbol={trade.symbol};"
                f"position_side={position_context['position_side']};"
                f"open_order_count={len(summaries)};"
                f"open_orders={json.dumps(summaries, separators=(',', ':'))}"
            )
        if not position_match["confirmed_flat"]:
            raise RuntimeError(
                "pre_entry_cleanup_blocked_active_position:"
                f"trade_id={trade.trade_id};"
                f"symbol={trade.symbol};"
                f"position_side={position_context['position_side']};"
                f"position_amt={self._format_number(position_match['position_amt'])};"
                f"open_order_count={len(summaries)};"
                f"open_orders={json.dumps(summaries, separators=(',', ':'))}"
            )

        await self._cancel_open_orders_for_symbol(
            client=client,
            trade=trade,
            symbol=trade.symbol,
            reason="pre_entry_cleanup",
            open_orders=open_orders,
            open_algo_orders=open_algo_orders,
            exchange_position_confirmed_flat=True,
        )

    async def _cleanup_exit_orders_after_close_if_flat(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
        reason: str,
    ) -> None:
        position_match = await self._match_position_for_context(
            client=client,
            trade=trade,
            position_context=position_context,
        )
        if position_match["ambiguous"] or not position_match["confirmed_flat"]:
            log.warning(
                "[binance-live] cleanup skipped trade_id=%s symbol=%s reason=%s confirmed_flat=%s ambiguous=%s positionSide=%s positionAmt=%s raw_positions=%s",
                trade.trade_id,
                trade.symbol,
                reason,
                position_match["confirmed_flat"],
                position_match["ambiguous"],
                position_context["position_side"],
                position_match["position_amt"],
                position_match["raw_positions"],
            )
            return

        open_orders, open_algo_orders = await self._list_open_orders(client, symbol=trade.symbol)
        await self._cancel_open_orders_for_symbol(
            client=client,
            trade=trade,
            symbol=trade.symbol,
            reason=reason,
            open_orders=open_orders,
            open_algo_orders=open_algo_orders,
            exchange_position_confirmed_flat=True,
        )

    async def _list_open_orders(self, client, *, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        open_orders = []
        open_algo_orders = []
        if hasattr(client, "get_open_orders"):
            rows = await client.get_open_orders(symbol=symbol)
            open_orders = rows if isinstance(rows, list) else []
        if hasattr(client, "get_open_algo_orders"):
            rows = await client.get_open_algo_orders(symbol=symbol)
            open_algo_orders = rows if isinstance(rows, list) else []
        return open_orders, open_algo_orders

    async def _cancel_open_orders_for_symbol(
        self,
        *,
        client,
        trade: Trade,
        symbol: str,
        reason: str,
        open_orders: list[dict[str, Any]],
        open_algo_orders: list[dict[str, Any]],
        exchange_position_confirmed_flat: bool,
    ) -> None:
        summaries = self._summarize_open_orders([*open_orders, *open_algo_orders])
        if not summaries:
            return

        if open_orders and hasattr(client, "cancel_all_orders"):
            await client.cancel_all_orders(symbol=symbol)
        if open_algo_orders and hasattr(client, "cancel_all_algo_orders"):
            await client.cancel_all_algo_orders(symbol=symbol)

        log.info(
            "[binance-live] cleanup stale exit orders trade_id=%s symbol=%s reason=%s open_order_count=%s canceled_orders=%s exchange_position_confirmed_flat=%s",
            trade.trade_id,
            symbol,
            reason,
            len(summaries),
            json.dumps(summaries, separators=(",", ":")),
            exchange_position_confirmed_flat,
        )

    async def _match_position_for_context(self, *, client, trade: Trade, position_context: dict[str, str]) -> dict[str, Any]:
        if not hasattr(client, "get_position_risk"):
            return {
                "ambiguous": True,
                "confirmed_flat": False,
                "position_amt": None,
                "raw_positions": [],
                "matched_position": None,
            }

        payload = await client.get_position_risk(symbol=trade.symbol)
        rows = self._position_rows_for_symbol(payload, trade.symbol)
        expected_side = str(position_context["position_side"]).upper()
        matched = [row for row in rows if self._position_side_matches(row, expected_side)]
        if not rows or not matched:
            return {
                "ambiguous": True,
                "confirmed_flat": False,
                "position_amt": None,
                "raw_positions": rows,
                "matched_position": None,
            }

        row = self._choose_position_row(matched)
        position_amt = self._to_float(row.get("positionAmt"))
        return {
            "ambiguous": False,
            "confirmed_flat": abs(position_amt) <= 1e-12,
            "position_amt": position_amt,
            "raw_positions": rows,
            "matched_position": row,
        }

    def _position_rows_for_symbol(self, payload: Any, symbol: str) -> list[dict[str, Any]]:
        if payload is None:
            return []
        rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        symbol_upper = str(symbol).upper()
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("symbol", "")).upper()
            if row_symbol and row_symbol != symbol_upper:
                continue
            result.append(dict(row))
        return result

    def _position_side_matches(self, row: dict[str, Any], expected_side: str) -> bool:
        row_side = self._string_or_none(row.get("positionSide") or row.get("position_side"))
        row_side = row_side.upper() if row_side else None
        if expected_side == "BOTH":
            return row_side in {None, "", "BOTH"}
        return row_side == expected_side

    def _choose_position_row(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        for row in rows:
            if abs(self._to_float(row.get("positionAmt"))) > 1e-12:
                return row
        return rows[0]

    async def _current_price_for_stop_validation(
        self,
        *,
        client,
        trade: Trade,
        position_context: dict[str, str],
    ) -> float:
        try:
            match = await self._match_position_for_context(
                client=client,
                trade=trade,
                position_context=position_context,
            )
        except Exception as exc:
            log.warning(
                "[binance-live] stop validation price fetch failed trade_id=%s symbol=%s error=%s:%s",
                trade.trade_id,
                trade.symbol,
                type(exc).__name__,
                exc,
            )
            return 0.0
        matched = match.get("matched_position")
        if isinstance(matched, dict):
            for key in ("markPrice", "lastPrice"):
                value = self._to_float(matched.get(key))
                if value > 0:
                    return value
        return 0.0

    def _stop_would_immediately_trigger(
        self,
        *,
        side: Side,
        stop_price: float,
        current_price: float,
    ) -> bool:
        stop = float(stop_price or 0.0)
        current = float(current_price or 0.0)
        if stop <= 0 or current <= 0:
            return False
        if side == Side.SHORT:
            return stop <= current
        return stop >= current

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

    def _build_tp_specs(
        self,
        *,
        trade: Trade,
        entry_price: float,
        qty: float,
        tick_size: float,
        qty_step: float,
    ) -> list[dict[str, Any]]:
        tp_levels = self._active_tp_levels(trade)
        if not tp_levels:
            return []

        entry_notional = max(float(qty), 0.0) * max(float(entry_price), 0.0)
        leverage = max(int(getattr(trade, "leverage", 1) or 1), 1)
        entry_margin_usdt = entry_notional / leverage if leverage > 0 else entry_notional
        planned_tp_levels = min(MAX_TP_LEVELS, len(tp_levels))
        original_tp_count = self._resolve_tp_ladder_count(
            qty=qty,
            entry_price=entry_price,
            qty_step=qty_step,
            planned_tp_levels=planned_tp_levels,
        )
        forced_by_small_margin = self._small_margin_requires_single_tp(entry_margin_usdt)
        tp_count = self._apply_small_margin_tp_count_rule(
            trade=trade,
            original_tp_count=original_tp_count,
            entry_margin_usdt=entry_margin_usdt,
            position_notional=entry_notional,
            leverage=leverage,
        )
        selected_levels = tp_levels[:tp_count]
        target_fractions = self._tp_close_fractions_for_count(tp_count)

        log.info(
            "[binance-live] tp ladder sizing trade_id=%s symbol=%s entry_notional=%s entry_margin_usdt=%s leverage=%s planned_tp_levels=%s original_tp_levels=%s adjusted_tp_levels=%s target_fractions=%s min_tp_notional=%s",
            trade.trade_id,
            trade.symbol,
            self._format_number(entry_notional),
            self._format_number(entry_margin_usdt),
            leverage,
            planned_tp_levels,
            original_tp_count,
            tp_count,
            target_fractions,
            self._format_number(self._min_tp_notional()),
        )

        specs: list[dict[str, Any]] = []
        allocated_qty = 0.0

        for index, level in enumerate(selected_levels, start=1):
            if index < tp_count:
                raw_qty = self._fractional_qty(qty, target_fractions[index - 1])
                tp_qty = self._round_qty_down(raw_qty, qty_step)
                allocated_qty = self._add_qty(allocated_qty, tp_qty)
            else:
                raw_qty = self._remaining_qty(qty, allocated_qty)
                tp_qty = self._round_qty_down(raw_qty, qty_step)

            raw_target_price, target_price = self._compute_tp_price_parts(
                side=trade.side,
                entry_price=entry_price,
                level=level,
                tick_size=tick_size,
            )

            specs.append(
                {
                    "stage": index,
                    "tp_count": tp_count,
                    "original_tp_count": original_tp_count,
                    "tp_count_forced_by_small_margin": forced_by_small_margin,
                    "tp_count_force_reason": (
                        "small_entry_margin_lt_1_usdt" if forced_by_small_margin else None
                    ),
                    "entry_margin_usdt": entry_margin_usdt,
                    "position_notional": entry_notional,
                    "leverage": leverage,
                    "level": level,
                    "target_close_fraction": target_fractions[index - 1],
                    "price": target_price,
                    "qty": tp_qty,
                    "close_fraction": (tp_qty / qty) if qty > 0 else 0.0,
                    "target_notional": tp_qty * entry_price,
                    "raw_price": raw_target_price,
                    "raw_qty": raw_qty,
                    "client_order_id": self._build_client_order_id(f"t{index}", trade.trade_id),
                }
            )

        return specs

    def _active_tp_levels(self, trade: Trade | None = None) -> tuple[float, ...]:
        if trade is not None:
            profile = self._exit_profile(trade)
            levels = tuple(float(level) for level in profile.tp_levels if float(level) > 0)
            max_levels = max(MIN_TP_LEVELS, min(int(profile.max_tp_count), int(self.config.max_tp_levels), MAX_TP_LEVELS))
            return levels[:max_levels]
        levels = tuple(float(level) for level in self.config.tp_levels if float(level) > 0)
        max_levels = max(MIN_TP_LEVELS, min(int(self.config.max_tp_levels), MAX_TP_LEVELS))
        return levels[:max_levels]

    def _exit_profile(self, trade: Trade) -> ExitProfile:
        return get_exit_profile(
            getattr(trade, "strategy_version", "v1"),
            signal_level=getattr(trade, "signal_level", None),
        )

    def _resolve_tp_ladder_count(
        self,
        *,
        qty: float,
        entry_price: float,
        qty_step: float,
        planned_tp_levels: int,
    ) -> int:
        min_levels = min(max(MIN_TP_LEVELS, int(self.config.min_tp_levels)), planned_tp_levels)
        max_levels = max(min_levels, min(MAX_TP_LEVELS, planned_tp_levels, int(self.config.max_tp_levels)))
        for tp_count in range(max_levels, min_levels - 1, -1):
            if self._tp_ladder_shape_is_valid(
                qty=qty,
                entry_price=entry_price,
                qty_step=qty_step,
                tp_count=tp_count,
            ):
                return tp_count
        return min_levels

    def _apply_small_margin_tp_count_rule(
        self,
        *,
        trade: Trade,
        original_tp_count: int,
        entry_margin_usdt: float,
        position_notional: float,
        leverage: int,
    ) -> int:
        threshold = self._small_margin_single_tp_threshold_usdt()
        final_tp_count = max(MIN_TP_LEVELS, min(MAX_TP_LEVELS, int(original_tp_count or MIN_TP_LEVELS)))
        if not self._small_margin_requires_single_tp(entry_margin_usdt):
            return final_tp_count

        log.info(
            "[binance-live] TP_COUNT_FORCED_BY_SMALL_MARGIN trade_id=%s symbol=%s entry_margin_usdt=%s leverage=%s position_notional=%s original_tp_count=%s final_tp_count=1 threshold_usdt=%s",
            trade.trade_id,
            trade.symbol,
            self._format_number(entry_margin_usdt),
            leverage,
            self._format_number(position_notional),
            final_tp_count,
            self._format_number(threshold),
        )
        return 1

    def _small_margin_requires_single_tp(self, entry_margin_usdt: float) -> bool:
        threshold = self._small_margin_single_tp_threshold_usdt()
        return threshold > 0 and float(entry_margin_usdt) < threshold

    def _tp_ladder_shape_is_valid(
        self,
        *,
        qty: float,
        entry_price: float,
        qty_step: float,
        tp_count: int,
    ) -> bool:
        min_tp_notional = self._min_tp_notional()
        allocated_qty = 0.0
        fractions = self._tp_close_fractions_for_count(tp_count)

        for index, fraction in enumerate(fractions, start=1):
            if index < tp_count:
                raw_qty = self._fractional_qty(qty, fraction)
                tp_qty = self._round_qty_down(raw_qty, qty_step)
                allocated_qty = self._add_qty(allocated_qty, tp_qty)
            else:
                tp_qty = self._round_qty_down(self._remaining_qty(qty, allocated_qty), qty_step)

            if tp_qty <= 0:
                return False
            if tp_count > 1 and min_tp_notional > 0 and (tp_qty * float(entry_price)) < min_tp_notional:
                return False

        return True

    @staticmethod
    def _fractional_qty(qty: float, fraction: float) -> float:
        return float(Decimal(str(max(float(qty), 0.0))) * Decimal(str(float(fraction))))

    @staticmethod
    def _remaining_qty(qty: float, allocated_qty: float) -> float:
        remaining = Decimal(str(max(float(qty), 0.0))) - Decimal(str(max(float(allocated_qty), 0.0)))
        return float(max(remaining, Decimal("0")))

    @staticmethod
    def _add_qty(left: float, right: float) -> float:
        return float(Decimal(str(max(float(left), 0.0))) + Decimal(str(max(float(right), 0.0))))

    @staticmethod
    def _tp_close_fractions_for_count(tp_count: int) -> tuple[float, ...]:
        tp_count = max(1, min(MAX_TP_LEVELS, int(tp_count)))
        if tp_count == 1:
            return (1.0,)
        equal_fraction = 1.0 / float(tp_count)
        return tuple(equal_fraction for _ in range(tp_count))

    def _min_tp_notional(self) -> float:
        try:
            return max(float(self.config.min_tp_notional), 0.0)
        except (TypeError, ValueError):
            return MIN_TP_NOTIONAL

    def _small_margin_single_tp_threshold_usdt(self) -> float:
        try:
            return max(float(self.config.small_margin_single_tp_threshold_usdt), 0.0)
        except (TypeError, ValueError):
            return SMALL_MARGIN_SINGLE_TP_THRESHOLD_USDT

    def _compute_tp_price(self, *, side: Side, entry_price: float, level: float, tick_size: float) -> float:
        _, rounded = self._compute_tp_price_parts(
            side=side,
            entry_price=entry_price,
            level=level,
            tick_size=tick_size,
        )
        return rounded

    def _compute_tp_price_parts(
        self,
        *,
        side: Side,
        entry_price: float,
        level: float,
        tick_size: float,
    ) -> tuple[float, float]:
        if side == Side.LONG:
            raw_price = entry_price * (1.0 + level)
            return raw_price, self._round_price(raw_price, tick_size, ROUND_CEILING)
        raw_price = entry_price * (1.0 - level)
        return raw_price, self._round_price(raw_price, tick_size, ROUND_FLOOR)

    def _compute_stop_price(
        self,
        *,
        side: Side,
        entry_price: float,
        stage: int,
        explicit_sl_price: float | None,
        tick_size: float,
        initial_stop_loss_pct: float | None = None,
    ) -> float:
        _, rounded = self._compute_stop_price_parts(
            side=side,
            entry_price=entry_price,
            stage=stage,
            explicit_sl_price=explicit_sl_price,
            tick_size=tick_size,
            initial_stop_loss_pct=initial_stop_loss_pct,
        )
        return rounded

    def _compute_stop_price_parts(
        self,
        *,
        side: Side,
        entry_price: float,
        stage: int,
        explicit_sl_price: float | None,
        tick_size: float,
        initial_stop_loss_pct: float | None = None,
    ) -> tuple[float, float]:
        initial_sl = (
            float(initial_stop_loss_pct)
            if initial_stop_loss_pct is not None
            else float(self.config.initial_stop_loss_pct)
        )
        if stage <= 0 and explicit_sl_price is not None:
            raw_price = float(explicit_sl_price)
            return raw_price, self._round_stop_price(side=side, raw_price=raw_price, tick_size=tick_size)
        if side == Side.LONG:
            if stage == 1:
                return entry_price, self._round_stop_price(side=side, raw_price=entry_price, tick_size=tick_size)
            if stage >= 2:
                raw_price = entry_price * 1.002
                return raw_price, self._round_stop_price(side=side, raw_price=raw_price, tick_size=tick_size)
            raw_price = entry_price * (1.0 - initial_sl)
            return raw_price, self._round_stop_price(side=side, raw_price=raw_price, tick_size=tick_size)
        if stage == 1:
            return entry_price, self._round_stop_price(side=side, raw_price=entry_price, tick_size=tick_size)
        if stage >= 2:
            raw_price = entry_price * 0.998
            return raw_price, self._round_stop_price(side=side, raw_price=raw_price, tick_size=tick_size)
        raw_price = entry_price * (1.0 + initial_sl)
        return raw_price, self._round_stop_price(side=side, raw_price=raw_price, tick_size=tick_size)

    def _round_stop_price(self, *, side: Side, raw_price: float, tick_size: float) -> float:
        rounding = ROUND_FLOOR if side == Side.LONG else ROUND_CEILING
        return self._round_price(raw_price, tick_size, rounding)

    def _round_qty_down(self, value: float, step: float) -> float:
        return self._round_number(value, step, ROUND_DOWN)

    def _ceil_to_step(self, value: float, step: float) -> float:
        return self._round_number(value, step, ROUND_CEILING)

    def _round_price(self, value: float, step: float, rounding_mode: str) -> float:
        return self._round_number(value, step, rounding_mode)

    def _round_number(self, value: float, step: float, rounding_mode: str) -> float:
        if step <= 0:
            return float(value)
        value_dec = Decimal(str(value))
        step_dec = Decimal(str(step))
        units = (value_dec / step_dec).to_integral_value(rounding=rounding_mode)
        return float(units * step_dec)

    def _entry_order_side(self, side: Side) -> str:
        return "BUY" if side == Side.LONG else "SELL"

    def _close_order_side(self, side: Side) -> str:
        return "SELL" if side == Side.LONG else "BUY"

    def _build_client_order_id(self, kind: str, trade_id: str) -> str:
        digest = hashlib.sha1(trade_id.encode("utf-8")).hexdigest()[:20]
        return f"mb-{kind}-{digest}"[:36]

    def _extract_avg_price(self, order: dict[str, Any], *, fallback: float) -> float:
        avg_price = self._to_float(order.get("avgPrice"), default=0.0)
        if avg_price > 0:
            return avg_price
        executed_qty = self._to_float(order.get("executedQty") or order.get("cumQty"), default=0.0)
        cum_quote = self._to_float(order.get("cumQuote") or order.get("cumQuoteQty"), default=0.0)
        if executed_qty > 0 and cum_quote > 0:
            return cum_quote / executed_qty
        return float(fallback)

    def _normalize_order_doc(self, order: dict[str, Any] | None) -> dict[str, Any] | None:
        if not order:
            return None
        return {
            "orderId": self._string_or_none(order.get("orderId")),
            "algoId": self._string_or_none(order.get("algoId")),
            "clientOrderId": self._string_or_none(order.get("clientOrderId") or order.get("clientAlgoId")),
            "clientAlgoId": self._string_or_none(order.get("clientAlgoId")),
            "status": self._string_or_none(order.get("status") or order.get("algoStatus")),
            "type": self._string_or_none(order.get("type") or order.get("orderType")),
            "algoType": self._string_or_none(order.get("algoType")),
            "positionSide": self._string_or_none(order.get("positionSide")),
            "origQty": self._to_float(order.get("origQty") or order.get("quantity")),
            "quantity": self._to_float(order.get("quantity") or order.get("origQty")),
            "executedQty": self._to_float(order.get("executedQty")),
            "avgPrice": self._to_float(order.get("avgPrice")),
            "stopPrice": self._to_float(order.get("stopPrice") or order.get("triggerPrice")),
            "triggerPrice": self._to_float(order.get("triggerPrice") or order.get("stopPrice")),
            "price": self._to_float(order.get("price")),
            "reduceOnly": order.get("reduceOnly"),
            "stage": order.get("stage"),
            "tp_count": order.get("tp_count"),
            "level": self._to_float(order.get("level")),
            "close_fraction": self._to_float(order.get("close_fraction")),
            "close_pct": self._to_float(order.get("close_pct")),
            "target_price": self._to_float(order.get("target_price")),
            "target_qty": self._to_float(order.get("target_qty")),
            "target_notional": self._to_float(order.get("target_notional")),
        }

    def _build_tp_plan(self, tp_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for order in sorted(tp_orders, key=lambda item: int(item.get("stage") or 0)):
            stage = int(order.get("stage") or len(plan) + 1)
            order_id = self._string_or_none(order.get("orderId"))
            algo_id = self._string_or_none(order.get("algoId"))
            plan.append(
                {
                    "stage": stage,
                    "tp_count": int(order.get("tp_count") or len(tp_orders)),
                    "level": self._to_float(order.get("level")),
                    "close_fraction": self._to_float(order.get("close_fraction")),
                    "close_pct": self._to_float(order.get("close_pct")),
                    "qty": self._to_float(order.get("target_qty") or order.get("origQty") or order.get("quantity")),
                    "price": self._to_float(order.get("target_price") or order.get("triggerPrice") or order.get("stopPrice")),
                    "triggerPrice": self._to_float(order.get("triggerPrice") or order.get("stopPrice") or order.get("target_price")),
                    "orderId": order_id,
                    "algoId": algo_id,
                    "clientOrderId": self._string_or_none(order.get("clientOrderId") or order.get("clientAlgoId")),
                    "clientAlgoId": self._string_or_none(order.get("clientAlgoId")),
                }
            )
        return plan

    def _normalize_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        if hasattr(value, "items"):
            try:
                return dict(value)
            except (TypeError, ValueError):
                return {}
        return {}

    def _is_hedge_position_mode(self, payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            raise RuntimeError("position_mode_unavailable")
        raw = payload.get("dualSidePosition")
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() == "true"

    def _position_side_for_trade(self, *, trade: Trade, position_mode: str) -> str:
        normalized_mode = str(position_mode).strip().lower()
        stored_side = self._stored_position_side(trade)
        if normalized_mode == "one_way":
            return "BOTH"
        if normalized_mode != "hedge":
            raise RuntimeError(f"unsupported_position_mode:{position_mode}")
        if stored_side in {"LONG", "SHORT"}:
            return stored_side
        return "LONG" if trade.side == Side.LONG else "SHORT"

    def _stored_position_side(self, trade: Trade) -> str | None:
        stored = self._string_or_none(getattr(trade, "exchange_position_side", None))
        if stored:
            return stored.upper()
        stop_order = getattr(trade, "exchange_stop_order", None) or {}
        stored = self._string_or_none(stop_order.get("positionSide"))
        return stored.upper() if stored else None

    def _validate_position_side(self, *, position_mode: str, position_side: str) -> None:
        normalized_mode = str(position_mode).strip().lower()
        normalized_side = str(position_side).strip().upper()
        if normalized_mode == "hedge" and normalized_side not in {"LONG", "SHORT"}:
            raise RuntimeError(f"invalid_position_side_for_hedge:{normalized_side}")
        if normalized_mode == "one_way" and normalized_side != "BOTH":
            raise RuntimeError(f"invalid_position_side_for_one_way:{normalized_side}")
        if normalized_mode not in {"hedge", "one_way"}:
            raise RuntimeError(f"unsupported_position_mode:{position_mode}")

    def _reduce_only_params(self, position_mode: str) -> dict[str, str]:
        if str(position_mode).strip().lower() == "hedge":
            return {}
        return {"reduceOnly": "true"}

    def _stop_replacement_lock_key(self, *, trade: Trade, position_side: str) -> str:
        return ":".join(
            [
                str(trade.user_id),
                str(trade.symbol).upper(),
                str(position_side).upper(),
                str(trade.trade_id),
            ]
        )

    def _stop_cancel_key(self, algo_id: str | None, client_algo_id: str | None) -> str | None:
        if algo_id:
            return f"algo:{algo_id}"
        if client_algo_id:
            return f"client:{client_algo_id}"
        return None

    def _algo_cancel_status(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "cancelled"
        status = str(payload.get("status") or "").strip().lower()
        if status in {"already_missing", "already_cancelled", "not_found"}:
            return "already_missing"
        code = payload.get("code")
        try:
            if int(code) == -2011:
                return "already_missing"
        except (TypeError, ValueError):
            pass
        return "cancelled"

    def _filter_replaced_stop_order_ids(
        self,
        order_ids: list[str],
        stop_order: dict[str, Any] | None,
    ) -> list[str]:
        stop_order = stop_order or {}
        stale_ids = {
            self._string_or_none(stop_order.get("orderId")),
            self._string_or_none(stop_order.get("algoId")),
            self._string_or_none(stop_order.get("clientOrderId")),
            self._string_or_none(stop_order.get("clientAlgoId")),
        }
        stale_ids.discard(None)
        return [order_id for order_id in order_ids if self._string_or_none(order_id) not in stale_ids]

    def _dedupe_order_ids(self, values: list[str | None]) -> list[str]:
        seen: set[str] = set()
        order_ids: list[str] = []
        for value in values:
            item = self._string_or_none(value)
            if not item or item in seen:
                continue
            seen.add(item)
            order_ids.append(item)
        return order_ids

    def _order_identifier(self, order: dict[str, Any] | None) -> str | None:
        if not order:
            return None
        return self._string_or_none(order.get("orderId") or order.get("algoId"))

    def _format_number(self, value: float) -> str:
        text = f"{float(value):.12f}"
        return text.rstrip("0").rstrip(".")

    def _format_exchange_number(self, value: float, step: float) -> str:
        if step <= 0:
            return self._format_number(value)
        step_dec = Decimal(str(step))
        value_dec = Decimal(str(value))
        units = (value_dec / step_dec).to_integral_value(rounding=ROUND_HALF_UP)
        quantized = units * step_dec
        text = format(quantized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _log_precision_details(
        self,
        *,
        symbol: str,
        order_kind: str,
        raw_qty: float | None = None,
        rounded_qty: float | None = None,
        step_size: float | None = None,
        raw_price: float | None = None,
        rounded_price: float | None = None,
        tick_size: float | None = None,
    ) -> None:
        formatted_qty = (
            self._format_exchange_number(rounded_qty, step_size)
            if rounded_qty is not None and step_size is not None
            else None
        )
        formatted_price = (
            self._format_exchange_number(rounded_price, tick_size)
            if rounded_price is not None and tick_size is not None
            else None
        )
        log.info(
            "[binance-live] precision symbol=%s order_kind=%s raw_qty=%s rounded_qty=%s formatted_qty=%s stepSize=%s raw_price=%s rounded_price=%s formatted_price=%s tickSize=%s",
            symbol,
            order_kind,
            self._format_number(raw_qty) if raw_qty is not None else None,
            self._format_number(rounded_qty) if rounded_qty is not None else None,
            formatted_qty,
            self._format_number(step_size) if step_size is not None else None,
            self._format_number(raw_price) if raw_price is not None else None,
            self._format_number(rounded_price) if rounded_price is not None else None,
            formatted_price,
            self._format_number(tick_size) if tick_size is not None else None,
        )

    def _string_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _to_float(self, value: Any, *, default: float = 0.0) -> float:
        try:
            if value is None:
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)
