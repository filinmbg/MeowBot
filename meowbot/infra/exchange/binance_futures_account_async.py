from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from meowbot.infra.exchange.binance_request_gate import BinanceGateConfig, BinanceRequestGate


log = logging.getLogger("meowbot")
_GLOBAL_INCOME_REQUEST_SEMAPHORE = asyncio.Semaphore(2)


class ExchangeStateFetchError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class BinanceFuturesAccountAsyncConfig:
    futures_base_url: str = "https://fapi.binance.com"
    wallet_base_url: str = "https://api.binance.com"
    timeout_seconds: float = 15.0

    max_weight_per_minute: int = 600
    max_concurrent_requests: int = 2

    retry_attempts: int = 5
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 15.0

    recv_window_ms: int = 30000
    min_recv_window_ms: int = 30000
    signed_request_safety_margin_ms: int = 3000
    time_sync_high_rtt_ms: int = 1000
    time_sync_degraded_rtt_ms: int = 5000
    time_sync_reject_rtt_ms: int = 3000
    time_sync_offset_jump_ms: int = 1000
    time_sync_offset_jump_confirmations: int = 3


@dataclass(slots=True)
class BinanceTimeSyncState:
    stable_offset_ms: int = 0
    last_good_offset_ms: int = 0
    last_binance_server_time_ms: int | None = None
    last_time_sync_monotonic: float = 0.0
    last_rtt_ms: int = 0
    safety_bias_ms: int = 3000
    sync_confidence: str = "unknown"
    healthy: bool = True
    pending_offset_ms: int | None = None
    pending_success_count: int = 0
    lock: asyncio.Lock | None = None

    def sync_lock(self) -> asyncio.Lock:
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock


_TIME_SYNC_STATES: dict[str, BinanceTimeSyncState] = {}
_TIME_SYNC_ALERT_LAST_AT: dict[str, float] = {}


def _time_sync_state_for(path: str) -> BinanceTimeSyncState:
    state = _TIME_SYNC_STATES.get(path)
    if state is None:
        state = BinanceTimeSyncState()
        _TIME_SYNC_STATES[path] = state
    return state


def reset_binance_time_sync_state_for_tests() -> None:
    _TIME_SYNC_STATES.clear()
    _TIME_SYNC_ALERT_LAST_AT.clear()


class BinanceTimeSyncUnhealthyError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class BinanceFuturesAccountAsync:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        config: BinanceFuturesAccountAsyncConfig | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.config = config or BinanceFuturesAccountAsyncConfig()

        limits = httpx.Limits(
            max_connections=self.config.max_concurrent_requests,
            max_keepalive_connections=self.config.max_concurrent_requests,
        )

        self._futures_client = httpx.AsyncClient(
            base_url=self.config.futures_base_url,
            timeout=self.config.timeout_seconds,
            limits=limits,
            headers={"X-MBX-APIKEY": self.api_key},
        )
        self._wallet_client = httpx.AsyncClient(
            base_url=self.config.wallet_base_url,
            timeout=self.config.timeout_seconds,
            limits=limits,
            headers={"X-MBX-APIKEY": self.api_key},
        )

        gate_cfg = BinanceGateConfig(
            max_weight_per_minute=self.config.max_weight_per_minute,
            max_concurrent_requests=self.config.max_concurrent_requests,
            retry_attempts=self.config.retry_attempts,
            retry_base_delay_seconds=self.config.retry_base_delay_seconds,
            retry_max_delay_seconds=self.config.retry_max_delay_seconds,
        )
        self._futures_gate = BinanceRequestGate(gate_cfg)
        self._wallet_gate = BinanceRequestGate(gate_cfg)
        self._futures_time_sync_state = _time_sync_state_for("/fapi/v1/time")
        self._wallet_time_sync_state = _time_sync_state_for("/api/v3/time")
        self._server_time_offset_ms: int = self._futures_time_sync_state.stable_offset_ms
        self._last_binance_server_time_ms: int | None = self._futures_time_sync_state.last_binance_server_time_ms
        self._last_time_sync_monotonic: float = self._futures_time_sync_state.last_time_sync_monotonic
        self._time_sync_lock = self._futures_time_sync_state.sync_lock()

    async def close(self) -> None:
        await self._futures_client.aclose()
        await self._wallet_client.aclose()

    async def get_account_info(self, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v3/account",
                params={},
                weight=5,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("[binance-account] get_account_info failed: %s: %s", type(exc).__name__, exc)
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_account_info_failed", None, exc)
            return None

    async def get_exchange_info(self) -> dict[str, Any] | None:
        try:
            payload = await self._request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/exchangeInfo",
                params={},
                weight=1,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("[binance-account] get_exchange_info failed: %s: %s", type(exc).__name__, exc)
            return None

    async def get_symbol_trading_rules(self, symbol: str) -> dict[str, float] | None:
        exchange_info = await self.get_exchange_info()
        if not exchange_info:
            return None

        symbol_upper = symbol.upper()
        for row in exchange_info.get("symbols", []):
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol", "")).upper() != symbol_upper:
                continue

            qty_step = 0.0
            min_qty = 0.0
            min_notional = 0.0
            for item in row.get("filters", []):
                if not isinstance(item, dict):
                    continue
                filter_type = str(item.get("filterType") or "")
                if filter_type in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
                    qty_step = self._to_float(item.get("stepSize"), default=qty_step)
                    min_qty = self._to_float(item.get("minQty"), default=min_qty)
                if filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
                    min_notional = self._to_float(
                        item.get("notional") or item.get("minNotional"),
                        default=min_notional,
                    )

            return {
                "qty_step": max(float(qty_step), 0.0),
                "min_qty": max(float(min_qty), 0.0),
                "min_notional": max(float(min_notional), 0.0),
            }

        return None

    async def get_api_key_permissions(self) -> dict[str, Any] | None:
        try:
            payload = await self._signed_request_json(
                client=self._wallet_client,
                gate=self._wallet_gate,
                method="GET",
                path="/sapi/v1/account/apiRestrictions",
                params={},
                weight=1,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("[binance-account] get_api_key_permissions failed: %s: %s", type(exc).__name__, exc)
            return None

    async def get_account_risk_snapshot(self) -> dict[str, Any] | None:
        account = await self.get_account_info()
        if not account:
            return None

        try:
            total_wallet_balance = float(account.get("totalWalletBalance", 0.0) or 0.0)
            total_margin_balance = float(account.get("totalMarginBalance", 0.0) or 0.0)
            total_maint_margin = float(account.get("totalMaintMargin", 0.0) or 0.0)
            available_balance = float(account.get("availableBalance", 0.0) or 0.0)
            total_unrealized_profit = float(account.get("totalUnrealizedProfit", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None

        margin_ratio_pct = None
        if total_margin_balance > 0:
            margin_ratio_pct = (total_maint_margin / total_margin_balance) * 100.0

        return {
            "availableBalance": available_balance,
            "totalWalletBalance": total_wallet_balance,
            "totalMarginBalance": total_margin_balance,
            "totalMaintMargin": total_maint_margin,
            "totalUnrealizedProfit": total_unrealized_profit,
            "marginRatioPct": margin_ratio_pct,
        }

    async def get_leverage_bracket(self, symbol: str) -> list[dict[str, Any]] | dict[str, Any] | None:
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/leverageBracket",
                params={"symbol": symbol.upper()},
                weight=1,
            )
            return payload
        except Exception as exc:
            log.warning(
                "[binance-account] get_leverage_bracket failed symbol=%s: %s: %s",
                symbol,
                type(exc).__name__,
                exc,
            )
            return None

    async def get_symbol_max_leverage(self, symbol: str) -> int | None:
        payload = await self.get_leverage_bracket(symbol)
        if payload is None:
            return None

        symbol_upper = symbol.upper()
        candidates: list[int] = []

        if isinstance(payload, dict):
            brackets = payload.get("brackets")
            if isinstance(brackets, list):
                for item in brackets:
                    if not isinstance(item, dict):
                        continue
                    try:
                        candidates.append(int(item.get("initialLeverage")))
                    except (TypeError, ValueError):
                        continue
        elif isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                row_symbol = str(row.get("symbol", "")).upper()
                if row_symbol and row_symbol != symbol_upper:
                    continue
                brackets = row.get("brackets")
                if not isinstance(brackets, list):
                    continue
                for item in brackets:
                    if not isinstance(item, dict):
                        continue
                    try:
                        candidates.append(int(item.get("initialLeverage")))
                    except (TypeError, ValueError):
                        continue

        return max(candidates) if candidates else None

    async def get_position_risk(
        self,
        symbol: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v2/positionRisk",
                params=params,
                weight=5,
            )
            if isinstance(payload, list):
                if symbol:
                    return [
                        row
                        for row in payload
                        if isinstance(row, dict) and str(row.get("symbol", "")).upper() == symbol.upper()
                    ]
                return payload
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("[binance-account] get_position_risk failed symbol=%s: %s: %s", symbol, type(exc).__name__, exc)
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_position_risk_failed", symbol, exc)
            return None

    async def get_open_orders(
        self,
        symbol: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/openOrders",
                params=params,
                weight=1 if symbol else 40,
            )
            return payload if isinstance(payload, list) else None
        except Exception as exc:
            log.warning("[binance-account] get_open_orders failed symbol=%s: %s: %s", symbol, type(exc).__name__, exc)
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_open_orders_failed", symbol, exc)
            return None

    async def get_all_orders(
        self,
        symbol: str,
        *,
        limit: int = 50,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | None:
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/allOrders",
                params={"symbol": symbol.upper(), "limit": int(limit)},
                weight=5,
            )
            return payload if isinstance(payload, list) else None
        except Exception as exc:
            log.warning("[binance-account] get_all_orders failed symbol=%s: %s: %s", symbol, type(exc).__name__, exc)
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_all_orders_failed", symbol, exc)
            return None

    async def get_open_algo_orders(
        self,
        symbol: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {"algoType": "CONDITIONAL"}
        if symbol:
            params["symbol"] = symbol.upper()
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/openAlgoOrders",
                params=params,
                weight=1 if symbol else 40,
            )
            return payload if isinstance(payload, list) else None
        except Exception as exc:
            log.warning(
                "[binance-account] get_open_algo_orders failed symbol=%s: %s: %s",
                symbol,
                type(exc).__name__,
                exc,
            )
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_open_algo_orders_failed", symbol, exc)
            return None

    async def get_all_algo_orders(
        self,
        symbol: str,
        *,
        limit: int = 50,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | None:
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/allAlgoOrders",
                params={"symbol": symbol.upper(), "limit": int(limit)},
                weight=5,
            )
            return payload if isinstance(payload, list) else None
        except Exception as exc:
            log.warning(
                "[binance-account] get_all_algo_orders failed symbol=%s: %s: %s",
                symbol,
                type(exc).__name__,
                exc,
            )
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_all_algo_orders_failed", symbol, exc)
            return None

    async def get_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        if "orderId" not in params and "origClientOrderId" not in params:
            raise ValueError("order_id or client_order_id is required")

        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="GET",
                path="/fapi/v1/order",
                params=params,
                weight=1,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("[binance-account] get_order failed symbol=%s: %s: %s", symbol, type(exc).__name__, exc)
            return None

    async def get_income_history(
        self,
        *,
        symbol: str,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "limit": max(1, min(int(limit), 1000)),
        }
        if income_type:
            params["incomeType"] = str(income_type).upper()
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)

        try:
            async with _GLOBAL_INCOME_REQUEST_SEMAPHORE:
                payload = await self._signed_request_json(
                    client=self._futures_client,
                    gate=self._futures_gate,
                    method="GET",
                    path="/fapi/v1/income",
                    params=params,
                    weight=30,
                )
            return payload if isinstance(payload, list) else None
        except Exception as exc:
            log.warning(
                "[binance-account] get_income_history failed symbol=%s income_type=%s: %s: %s",
                symbol,
                income_type,
                type(exc).__name__,
                exc,
            )
            if raise_on_error:
                self._raise_exchange_state_fetch_error("get_income_history_failed", symbol, exc)
            return None

    async def change_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/leverage",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="POST",
            path="/fapi/v1/leverage",
            params={"symbol": symbol.upper(), "leverage": int(leverage)},
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def ensure_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        symbol_upper = symbol.upper()
        desired_leverage = int(leverage)
        positions = await self.get_position_risk(symbol=symbol_upper)
        current_leverage = self._extract_current_leverage(positions)
        if current_leverage == desired_leverage:
            return {
                "status": "already_set",
                "symbol": symbol_upper,
                "current_leverage": current_leverage,
                "desired_leverage": desired_leverage,
                "attempted": False,
            }

        open_orders = await self.get_open_orders(symbol=symbol_upper) or []
        open_algo_orders = await self.get_open_algo_orders(symbol=symbol_upper) or []
        open_order_summaries = self._summarize_open_orders([*open_orders, *open_algo_orders])
        if open_order_summaries:
            raise RuntimeError(
                "leverage_change_blocked_by_open_orders:"
                f"current_leverage={current_leverage};"
                f"desired_leverage={desired_leverage};"
                f"open_order_count={len(open_order_summaries)};"
                f"open_orders={json.dumps(open_order_summaries, separators=(',', ':'))}"
            )

        payload = await self.change_leverage(symbol=symbol_upper, leverage=desired_leverage)
        payload.update(
            {
                "current_leverage": current_leverage,
                "desired_leverage": desired_leverage,
                "attempted": True,
            }
        )
        return payload

    async def get_position_mode(self) -> dict[str, Any]:
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="GET",
            path="/fapi/v1/positionSide/dual",
            params={},
            weight=30,
        )
        assert isinstance(payload, dict)
        return payload

    async def ensure_one_way_mode(self) -> dict[str, Any]:
        try:
            await self._ensure_time_sync_healthy_for_mutation(
                client=self._futures_client,
                gate=self._futures_gate,
                path="/fapi/v1/positionSide/dual",
            )
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="POST",
                path="/fapi/v1/positionSide/dual",
                params={"dualSidePosition": "false"},
                weight=1,
            )
            return payload if isinstance(payload, dict) else {"status": "ok"}
        except httpx.HTTPStatusError as exc:
            code = self._extract_binance_error_code(exc.response)
            if code in {-4059}:
                return {"status": "already_one_way", "code": code}
            raise

    async def ensure_cross_margin(self, *, symbol: str) -> dict[str, Any]:
        symbol_upper = symbol.upper()
        desired_margin_type = "CROSSED"
        positions = await self.get_position_risk(symbol=symbol_upper)
        current_margin_type = self._extract_current_margin_type(positions)
        if self._is_cross_margin_type(current_margin_type):
            return {
                "status": "already_cross",
                "symbol": symbol_upper,
                "current_margin_type": current_margin_type,
                "desired_margin_type": desired_margin_type,
                "open_order_count": 0,
                "attempted": False,
                "skipped": True,
            }

        open_orders = await self.get_open_orders(symbol=symbol_upper) or []
        open_algo_orders = await self.get_open_algo_orders(symbol=symbol_upper) or []
        open_order_summaries = self._summarize_open_orders([*open_orders, *open_algo_orders])
        if open_order_summaries:
            raise RuntimeError(
                "margin_type_change_blocked_by_open_orders:"
                f"current_margin_type={current_margin_type};"
                f"desired_margin_type={desired_margin_type};"
                f"open_order_count={len(open_order_summaries)};"
                f"open_orders={json.dumps(open_order_summaries, separators=(',', ':'))}"
            )

        try:
            await self._ensure_time_sync_healthy_for_mutation(
                client=self._futures_client,
                gate=self._futures_gate,
                path="/fapi/v1/marginType",
            )
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="POST",
                path="/fapi/v1/marginType",
                params={"symbol": symbol_upper, "marginType": desired_margin_type},
                weight=1,
            )
            result = payload if isinstance(payload, dict) else {"status": "ok"}
            result.update(
                {
                    "current_margin_type": current_margin_type,
                    "desired_margin_type": desired_margin_type,
                    "open_order_count": 0,
                    "attempted": True,
                    "skipped": False,
                }
            )
            return result
        except httpx.HTTPStatusError as exc:
            code = self._extract_binance_error_code(exc.response)
            if code in {-4046, -4047}:
                return {
                    "status": "already_cross",
                    "code": code,
                    "current_margin_type": current_margin_type,
                    "desired_margin_type": desired_margin_type,
                    "open_order_count": 0,
                    "attempted": True,
                    "skipped": False,
                }
            raise

    async def create_order(self, **params: Any) -> dict[str, Any]:
        log.info("[binance-account] create_order params=%s", self._sanitize_params(params))
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/order",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="POST",
            path="/fapi/v1/order",
            params=params,
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def create_algo_order(self, **params: Any) -> dict[str, Any]:
        log.info("[binance-account] create_algo_order params=%s", self._sanitize_params(params))
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/algoOrder",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="POST",
            path="/fapi/v1/algoOrder",
            params=params,
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/order",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="DELETE",
            path="/fapi/v1/order",
            params=params,
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def cancel_algo_order(
        self,
        *,
        algo_id: str | None = None,
        client_algo_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if algo_id:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        if "algoId" not in params and "clientAlgoId" not in params:
            raise ValueError("algo_id or client_algo_id is required")

        log.info("[binance-account] cancel_algo_order params=%s", self._sanitize_params(params))
        try:
            await self._ensure_time_sync_healthy_for_mutation(
                client=self._futures_client,
                gate=self._futures_gate,
                path="/fapi/v1/algoOrder",
            )
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                method="DELETE",
                path="/fapi/v1/algoOrder",
                params=params,
                weight=1,
            )
        except httpx.HTTPStatusError as exc:
            code = self._extract_binance_error_code(exc.response)
            message = self._extract_binance_error_message(exc.response)
            if code == -2011:
                log.info(
                    "[binance-account] cancel_algo_order already missing params=%s code=%s msg=%s",
                    self._sanitize_params(params),
                    code,
                    message,
                )
                return {
                    "status": "already_missing",
                    "code": code,
                    "msg": message or "Unknown order sent.",
                    **params,
                }
            raise
        assert isinstance(payload, dict)
        return payload

    async def cancel_all_orders(self, *, symbol: str) -> dict[str, Any]:
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/allOpenOrders",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="DELETE",
            path="/fapi/v1/allOpenOrders",
            params={"symbol": symbol.upper()},
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def cancel_all_algo_orders(self, *, symbol: str) -> dict[str, Any]:
        await self._ensure_time_sync_healthy_for_mutation(
            client=self._futures_client,
            gate=self._futures_gate,
            path="/fapi/v1/algoOpenOrders",
        )
        payload = await self._signed_request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="DELETE",
            path="/fapi/v1/algoOpenOrders",
            params={"symbol": symbol.upper()},
            weight=1,
        )
        assert isinstance(payload, dict)
        return payload

    async def start_user_data_stream(self) -> str:
        payload = await self._request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="POST",
            path="/fapi/v1/listenKey",
            params={},
            weight=1,
        )
        if not isinstance(payload, dict) or not payload.get("listenKey"):
            raise RuntimeError("listenKey missing in Binance response")
        return str(payload["listenKey"])

    async def keepalive_user_data_stream(self, *, listen_key: str) -> dict[str, Any]:
        payload = await self._request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="PUT",
            path="/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            weight=1,
        )
        return payload if isinstance(payload, dict) else {"status": "ok"}

    async def close_user_data_stream(self, *, listen_key: str) -> dict[str, Any]:
        payload = await self._request_json(
            client=self._futures_client,
            gate=self._futures_gate,
            method="DELETE",
            path="/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            weight=1,
        )
        return payload if isinstance(payload, dict) else {"status": "ok"}

    async def _request_json(
        self,
        *,
        client: httpx.AsyncClient,
        gate: BinanceRequestGate,
        method: str,
        path: str,
        params: dict[str, Any],
        weight: int,
    ) -> dict | list:
        return await gate.execute_json(
            client=client,
            method=method,
            path=path,
            params=params,
            weight=weight,
        )

    @property
    def binance_time_sync_ok(self) -> bool:
        return bool(self._futures_time_sync_state.healthy)

    def time_sync_health(self) -> dict[str, Any]:
        return self._time_sync_health_dict(self._futures_time_sync_state)

    def _time_sync_state_for_client(self, client: httpx.AsyncClient) -> BinanceTimeSyncState:
        if self._time_path_for_client(client) == "/api/v3/time":
            return getattr(self, "_wallet_time_sync_state", _time_sync_state_for("/api/v3/time"))
        return getattr(self, "_futures_time_sync_state", _time_sync_state_for("/fapi/v1/time"))

    def _time_sync_health_dict(self, state: BinanceTimeSyncState) -> dict[str, Any]:
        return {
            "binance_time_sync_ok": bool(state.healthy),
            "stable_offset_ms": int(state.stable_offset_ms),
            "last_good_offset_ms": int(state.last_good_offset_ms),
            "last_rtt_ms": int(state.last_rtt_ms),
            "safety_bias_ms": int(state.safety_bias_ms),
            "sync_confidence": state.sync_confidence,
            "last_binance_server_time_ms": state.last_binance_server_time_ms,
        }

    async def _ensure_time_sync_healthy_for_mutation(
        self,
        *,
        client: httpx.AsyncClient,
        gate: BinanceRequestGate,
        path: str,
    ) -> None:
        state = self._time_sync_state_for_client(client)
        if state.healthy:
            return

        sync_payload: dict[str, Any] | None = None
        try:
            sync_payload = await self._sync_server_time(
                client=client,
                gate=gate,
                expected_offset_ms=state.stable_offset_ms,
                force=True,
            )
        except Exception as exc:
            self._log_time_sync_alert(
                "unhealthy",
                "[binance-time-sync] Binance time sync unhealthy, live entries paused path=%s error=%s:%s",
                path,
                type(exc).__name__,
                exc,
            )

        if state.healthy:
            log.info(
                "[binance-time-sync] Binance time sync recovered, live entries resumed path=%s stable_offset_ms=%s rtt_ms=%s safety_bias_ms=%s confidence=%s",
                path,
                state.stable_offset_ms,
                state.last_rtt_ms,
                state.safety_bias_ms,
                state.sync_confidence,
            )
            return

        details = {
            "path": path,
            **self._time_sync_health_dict(state),
        }
        if sync_payload:
            details.update({f"sync_{key}": value for key, value in sync_payload.items()})
        self._log_time_sync_alert(
            "unhealthy",
            "[binance-time-sync] Binance time sync unhealthy, live entries paused path=%s rtt_ms=%s stable_offset_ms=%s safety_bias_ms=%s confidence=%s",
            path,
            state.last_rtt_ms,
            state.stable_offset_ms,
            state.safety_bias_ms,
            state.sync_confidence,
        )
        raise BinanceTimeSyncUnhealthyError(
            "binance_time_sync_unhealthy_live_entries_paused",
            details=details,
        )

    @staticmethod
    def _log_time_sync_alert(alert_key: str, message: str, *args: Any) -> None:
        now = time.monotonic()
        last_at = _TIME_SYNC_ALERT_LAST_AT.get(alert_key, 0.0)
        if now - last_at < 600.0:
            log.debug(message, *args)
            return
        _TIME_SYNC_ALERT_LAST_AT[alert_key] = now
        log.warning(message, *args)

    async def _signed_request_json(
        self,
        *,
        client: httpx.AsyncClient,
        gate: BinanceRequestGate,
        method: str,
        path: str,
        params: dict[str, Any],
        weight: int,
    ) -> dict | list:
        signing_context: dict[str, Any] = {}

        def build_signed_params() -> dict[str, Any]:
            state = self._time_sync_state_for_client(client)
            signed_params = dict(params)
            local_time_ms = int(time.time() * 1000)
            stable_offset_ms = int(state.stable_offset_ms)
            self._server_time_offset_ms = stable_offset_ms
            binance_server_time_ms = int(local_time_ms + stable_offset_ms)
            safety_margin_ms = self._signed_request_safety_margin_ms(state=state)
            recv_window_ms = self._recv_window_ms()
            final_timestamp = max(0, binance_server_time_ms - safety_margin_ms)
            signed_params["timestamp"] = final_timestamp
            signed_params["recvWindow"] = recv_window_ms

            query_string = urlencode(signed_params, doseq=True)
            signature = hmac.new(self.api_secret, query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            signed_params["signature"] = signature

            signing_context.clear()
            signing_context.update(
                {
                    "timestamp_ms": final_timestamp,
                    "final_timestamp": final_timestamp,
                    "local_time_ms": local_time_ms,
                    "binance_server_time_ms": binance_server_time_ms,
                    "request_age_ms": max(0, binance_server_time_ms - final_timestamp),
                    "server_time_offset_ms": stable_offset_ms,
                    "stable_offset_ms": stable_offset_ms,
                    "last_good_offset_ms": state.last_good_offset_ms,
                    "last_rtt_ms": state.last_rtt_ms,
                    "safety_bias_ms": state.safety_bias_ms,
                    "safety_margin_ms": safety_margin_ms,
                    "sync_confidence": state.sync_confidence,
                    "binance_time_sync_ok": state.healthy,
                    "recv_window_ms": recv_window_ms,
                }
            )
            return signed_params

        try:
            return await gate.execute_json(
                client=client,
                method=method,
                path=path,
                params_factory=build_signed_params,
                weight=weight,
            )
        except httpx.HTTPStatusError as exc:
            error_code = self._extract_binance_error_code(exc.response)
            if error_code != -1021:
                self._attach_time_context(exc, signing_context, retry_after_time_sync=False)
                raise

            time_path = self._time_path_for_client(client)
            state = self._time_sync_state_for_client(client)
            state.healthy = False
            state.sync_confidence = "degraded"
            log.warning(
                "[binance-1021] path=%s time_path=%s final_timestamp=%s estimated_server_time_ms=%s estimated_delta_ms=%s stable_offset_ms=%s safety_bias_ms=%s recv_window_ms=%s retry_after_time_sync=%s decision=%s",
                path,
                time_path,
                signing_context.get("final_timestamp"),
                signing_context.get("binance_server_time_ms"),
                (
                    int(signing_context.get("binance_server_time_ms") or 0)
                    - int(signing_context.get("final_timestamp") or 0)
                ),
                signing_context.get("stable_offset_ms"),
                signing_context.get("safety_margin_ms"),
                signing_context.get("recv_window_ms"),
                True,
                "retry",
            )
            sync_payload = await self._sync_server_time(
                client=client,
                gate=gate,
                expected_offset_ms=signing_context.get("server_time_offset_ms"),
                force=True,
            )
            try:
                retry_payload = await gate.execute_json(
                    client=client,
                    method=method,
                    path=path,
                    params_factory=build_signed_params,
                    weight=weight,
                )
            except Exception as retry_exc:
                if (
                    isinstance(retry_exc, httpx.HTTPStatusError)
                    and self._extract_binance_error_code(retry_exc.response) == -1021
                ):
                    state.healthy = False
                    state.sync_confidence = "degraded"
                self._attach_time_context(
                    retry_exc,
                    signing_context,
                    retry_after_time_sync=True,
                    sync_payload=sync_payload,
                )
                log.warning(
                    "[binance-1021] retry failed path=%s local_time_ms=%s estimated_server_time_ms=%s stable_offset_ms=%s safety_bias_ms=%s final_timestamp=%s recv_window_ms=%s retry_after_time_sync=%s error=%s:%s decision=%s",
                    path,
                    signing_context.get("local_time_ms"),
                    signing_context.get("binance_server_time_ms"),
                    signing_context.get("stable_offset_ms"),
                    signing_context.get("safety_margin_ms"),
                    signing_context.get("final_timestamp"),
                    signing_context.get("recv_window_ms"),
                    True,
                    type(retry_exc).__name__,
                    retry_exc,
                    "skip_update",
                )
                raise
            state.healthy = True
            state.sync_confidence = "high" if state.last_rtt_ms <= self.config.time_sync_high_rtt_ms else "medium"
            log.info(
                "[binance-time-sync] Binance time sync recovered, live entries resumed path=%s local_time_ms=%s estimated_server_time_ms=%s stable_offset_ms=%s safety_bias_ms=%s final_timestamp=%s recv_window_ms=%s retry_after_time_sync=%s decision=%s",
                path,
                signing_context.get("local_time_ms"),
                signing_context.get("binance_server_time_ms"),
                signing_context.get("stable_offset_ms"),
                signing_context.get("safety_margin_ms"),
                signing_context.get("final_timestamp"),
                signing_context.get("recv_window_ms"),
                True,
                "accept",
            )
            return retry_payload

    def _current_timestamp_ms(self, *, local_time_ms: int | None = None) -> int:
        if local_time_ms is None:
            local_time_ms = int(time.time() * 1000)
        return max(
            0,
            self._estimated_binance_server_time_ms(local_time_ms=local_time_ms)
            - self._signed_request_safety_margin_ms(state=self._futures_time_sync_state),
        )

    def _estimated_binance_server_time_ms(self, *, local_time_ms: int) -> int:
        return int(local_time_ms + self._futures_time_sync_state.stable_offset_ms)

    def _signed_request_safety_margin_ms(self, *, state: BinanceTimeSyncState | None = None) -> int:
        configured = int(getattr(self.config, "signed_request_safety_margin_ms", 3000) or 0)
        dynamic_bias = int(state.safety_bias_ms) if state is not None else 3000
        return max(3000, configured, dynamic_bias)

    def _recv_window_ms(self) -> int:
        configured = int(getattr(self.config, "recv_window_ms", 30000) or 30000)
        minimum = int(getattr(self.config, "min_recv_window_ms", 30000) or 30000)
        return max(configured, minimum)

    def _attach_time_context(
        self,
        exc: Exception,
        signing_context: dict[str, Any],
        *,
        retry_after_time_sync: bool,
        sync_payload: dict[str, Any] | None = None,
    ) -> None:
        context = {
            "local_time_ms": signing_context.get("local_time_ms"),
            "binance_server_time_ms": signing_context.get("binance_server_time_ms"),
            "synced_binance_server_time_ms": (
                sync_payload.get("binance_server_time_ms") if isinstance(sync_payload, dict) else None
            ),
            "offset_before": sync_payload.get("offset_before") if isinstance(sync_payload, dict) else None,
            "offset_after": sync_payload.get("offset_after") if isinstance(sync_payload, dict) else None,
            "local_before": sync_payload.get("local_before") if isinstance(sync_payload, dict) else None,
            "local_after": sync_payload.get("local_after") if isinstance(sync_payload, dict) else None,
            "round_trip_ms": sync_payload.get("round_trip_ms") if isinstance(sync_payload, dict) else None,
            "estimated_local_at_response": (
                sync_payload.get("estimated_local_at_response") if isinstance(sync_payload, dict) else None
            ),
            "lock_wait_ms": sync_payload.get("lock_wait_ms") if isinstance(sync_payload, dict) else None,
            "retry_used_offset": signing_context.get("server_time_offset_ms"),
            "server_time_offset_ms": signing_context.get("server_time_offset_ms"),
            "stable_offset_ms": signing_context.get("stable_offset_ms"),
            "last_good_offset_ms": signing_context.get("last_good_offset_ms"),
            "last_rtt_ms": signing_context.get("last_rtt_ms"),
            "safety_bias_ms": signing_context.get("safety_bias_ms"),
            "sync_confidence": signing_context.get("sync_confidence"),
            "binance_time_sync_ok": signing_context.get("binance_time_sync_ok"),
            "new_offset_ms": sync_payload.get("new_offset_ms") if isinstance(sync_payload, dict) else None,
            "sync_healthy": sync_payload.get("sync_healthy") if isinstance(sync_payload, dict) else None,
            "sample_accepted": sync_payload.get("sample_accepted") if isinstance(sync_payload, dict) else None,
            "safety_margin_ms": signing_context.get("safety_margin_ms"),
            "final_timestamp": signing_context.get("final_timestamp"),
            "recvWindow": signing_context.get("recv_window_ms"),
            "retry_after_time_sync": retry_after_time_sync,
        }
        try:
            setattr(exc, "binance_time_context", context)
        except Exception:
            return

    def _time_path_for_client(self, client: httpx.AsyncClient) -> str:
        base_url = str(getattr(client, "base_url", ""))
        if "api.binance.com" in base_url and "fapi" not in base_url:
            return "/api/v3/time"
        return "/fapi/v1/time"

    async def _sync_server_time(
        self,
        *,
        client: httpx.AsyncClient,
        gate: BinanceRequestGate,
        expected_offset_ms: Any | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        state = self._time_sync_state_for_client(client)
        lock = state.sync_lock()
        lock_wait_started = time.monotonic()
        async with lock:
            lock_wait_ms = int((time.monotonic() - lock_wait_started) * 1000)
            offset_before = int(state.stable_offset_ms)
            expected_offset = self._int_or_none(expected_offset_ms)
            if (
                not force
                and
                expected_offset is not None
                and offset_before != expected_offset
                and (time.monotonic() - state.last_time_sync_monotonic) <= 3.0
            ):
                return {
                    "server_time_ms": state.last_binance_server_time_ms,
                    "binance_server_time_ms": state.last_binance_server_time_ms,
                    "local_time_ms": int(time.time() * 1000),
                    "server_time_offset_ms": state.stable_offset_ms,
                    "stable_offset_ms": state.stable_offset_ms,
                    "last_good_offset_ms": state.last_good_offset_ms,
                    "offset_before": expected_offset,
                    "offset_after": state.stable_offset_ms,
                    "lock_wait_ms": lock_wait_ms,
                    "round_trip_ms": 0,
                    "safety_margin_ms": self._signed_request_safety_margin_ms(state=state),
                    "safety_bias_ms": state.safety_bias_ms,
                    "sync_confidence": state.sync_confidence,
                    "sync_healthy": state.healthy,
                    "time_sync_reused": True,
                }

            local_before_ms = int(time.time() * 1000)
            monotonic_before = time.monotonic()
            time_path = self._time_path_for_client(client)
            payload = await gate.execute_json(
                client=client,
                method="GET",
                path=time_path,
                params={},
                weight=1,
            )
            monotonic_after = time.monotonic()
            local_after_ms = int(time.time() * 1000)
            if not isinstance(payload, dict) or payload.get("serverTime") is None:
                raise RuntimeError("server_time_sync_failed")
            server_time_ms = int(payload["serverTime"])
            round_trip_ms = int((monotonic_after - monotonic_before) * 1000)
            estimated_local_at_response = int((local_before_ms + local_after_ms) / 2)
            new_offset_ms = int(server_time_ms - estimated_local_at_response)
            safety_bias_ms = max(
                3000,
                int(round_trip_ms / 2) + 1000,
                int(getattr(self.config, "signed_request_safety_margin_ms", 3000) or 3000),
            )
            offset_delta_ms = abs(new_offset_ms - offset_before)
            high_rtt = round_trip_ms > int(self.config.time_sync_high_rtt_ms)
            reject_sample = round_trip_ms > int(self.config.time_sync_reject_rtt_ms)
            degraded = round_trip_ms > int(self.config.time_sync_degraded_rtt_ms)
            sample_accepted = False
            action = "keep_stable"

            state.last_binance_server_time_ms = server_time_ms
            state.last_time_sync_monotonic = monotonic_after
            state.last_rtt_ms = round_trip_ms
            state.safety_bias_ms = safety_bias_ms

            if reject_sample:
                state.sync_confidence = "degraded" if degraded else "low"
                state.healthy = not degraded
                action = "reject_high_rtt"
            elif state.sync_confidence == "unknown":
                state.stable_offset_ms = new_offset_ms
                state.last_good_offset_ms = new_offset_ms
                state.pending_offset_ms = None
                state.pending_success_count = 0
                state.sync_confidence = "medium" if high_rtt else "high"
                state.healthy = True
                sample_accepted = True
                action = "accept_initial"
            elif offset_delta_ms > int(self.config.time_sync_offset_jump_ms):
                if state.pending_offset_ms == new_offset_ms:
                    state.pending_success_count += 1
                else:
                    state.pending_offset_ms = new_offset_ms
                    state.pending_success_count = 1

                if state.pending_success_count >= int(self.config.time_sync_offset_jump_confirmations):
                    state.stable_offset_ms = new_offset_ms
                    state.last_good_offset_ms = new_offset_ms
                    state.pending_offset_ms = None
                    state.pending_success_count = 0
                    state.sync_confidence = "medium" if high_rtt else "high"
                    state.healthy = True
                    sample_accepted = True
                    action = "accept_confirmed_jump"
                else:
                    state.sync_confidence = "low" if high_rtt else "medium"
                    state.healthy = True
                    action = "pending_offset_confirmation"
            elif high_rtt:
                state.sync_confidence = "low"
                state.healthy = True
                action = "keep_stable_high_rtt"
            else:
                state.stable_offset_ms = new_offset_ms
                state.last_good_offset_ms = new_offset_ms
                state.pending_offset_ms = None
                state.pending_success_count = 0
                state.sync_confidence = "high"
                state.healthy = True
                sample_accepted = True
                action = "accept"

            self._server_time_offset_ms = int(state.stable_offset_ms)
            self._last_binance_server_time_ms = state.last_binance_server_time_ms
            self._last_time_sync_monotonic = state.last_time_sync_monotonic
            if reject_sample or offset_delta_ms > int(self.config.time_sync_offset_jump_ms):
                log.warning(
                    "[binance-time-sync] rtt_ms=%s stable_offset_ms=%s new_offset_ms=%s offset_delta_ms=%s safety_bias_ms=%s confidence=%s sync_healthy=%s action=%s pending_success_count=%s",
                    round_trip_ms,
                    state.stable_offset_ms,
                    new_offset_ms,
                    offset_delta_ms,
                    state.safety_bias_ms,
                    state.sync_confidence,
                    state.healthy,
                    action,
                    state.pending_success_count,
                )
            else:
                log.info(
                    "[binance-time-sync] rtt_ms=%s stable_offset_ms=%s new_offset_ms=%s offset_delta_ms=%s safety_bias_ms=%s confidence=%s sync_healthy=%s action=%s",
                    round_trip_ms,
                    state.stable_offset_ms,
                    new_offset_ms,
                    offset_delta_ms,
                    state.safety_bias_ms,
                    state.sync_confidence,
                    state.healthy,
                    action,
                )
            return {
                "server_time_ms": server_time_ms,
                "binance_server_time_ms": server_time_ms,
                "local_time_ms": local_after_ms,
                "local_before": local_before_ms,
                "local_after": local_after_ms,
                "round_trip_ms": round_trip_ms,
                "estimated_local_at_response": estimated_local_at_response,
                "offset_before": offset_before,
                "offset_after": state.stable_offset_ms,
                "new_offset_ms": new_offset_ms,
                "server_time_offset_ms": state.stable_offset_ms,
                "stable_offset_ms": state.stable_offset_ms,
                "last_good_offset_ms": state.last_good_offset_ms,
                "safety_margin_ms": self._signed_request_safety_margin_ms(state=state),
                "safety_bias_ms": state.safety_bias_ms,
                "sync_confidence": state.sync_confidence,
                "sync_healthy": state.healthy,
                "sample_accepted": sample_accepted,
                "pending_success_count": state.pending_success_count,
                "action": action,
                "lock_wait_ms": lock_wait_ms,
                "time_sync_reused": False,
            }

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_binance_error_code(response: httpx.Response | None) -> int | None:
        if response is None:
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        except Exception:
            return None
        try:
            return int(payload.get("code"))
        except Exception:
            return None

    @staticmethod
    def _extract_binance_error_message(response: httpx.Response | None) -> str | None:
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        message = payload.get("msg") if isinstance(payload, dict) else None
        return str(message) if message is not None else None

    def _raise_exchange_state_fetch_error(self, operation: str, symbol: str | None, exc: Exception) -> None:
        details = self._exchange_error_details(exc)
        details.update(
            {
                "operation": operation,
                "symbol": symbol,
            }
        )
        raise ExchangeStateFetchError(
            f"{operation}:{symbol}:{type(exc).__name__}:{exc}",
            details=details,
        ) from exc

    def _exchange_error_details(self, exc: Exception) -> dict[str, Any]:
        if not isinstance(exc, httpx.HTTPStatusError):
            return {}

        response = exc.response
        request = exc.request
        if response is not None:
            request = response.request or request

        http_path = None
        if request is not None:
            http_path = request.url.path

        return {
            "http_status": response.status_code if response is not None else None,
            "http_path": http_path,
            "exchange_error_code": self._extract_binance_error_code(response),
            "exchange_error_message": self._extract_binance_error_message(response),
            "response_body": self._safe_response_text(response),
            **self._exception_time_context(exc),
        }

    @staticmethod
    def _exception_time_context(exc: Exception) -> dict[str, Any]:
        context = getattr(exc, "binance_time_context", None)
        if not isinstance(context, dict):
            return {}
        return {key: value for key, value in context.items() if value is not None}

    @staticmethod
    def _safe_response_text(response: httpx.Response | None, *, limit: int = 1000) -> str | None:
        if response is None:
            return None
        try:
            text = str(response.text or "")
        except Exception:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
        sanitized = dict(params or {})
        for key in list(sanitized.keys()):
            if str(key).lower() == "signature":
                sanitized[key] = "<redacted>"
        return sanitized

    def _extract_current_margin_type(self, positions: list[dict[str, Any]] | dict[str, Any] | None) -> str | None:
        rows = positions if isinstance(positions, list) else [positions] if isinstance(positions, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            margin_type = row.get("marginType") or row.get("margin_type")
            if margin_type:
                return str(margin_type)
        return None

    def _extract_current_leverage(self, positions: list[dict[str, Any]] | dict[str, Any] | None) -> int | None:
        rows = positions if isinstance(positions, list) else [positions] if isinstance(positions, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                return int(row.get("leverage"))
            except (TypeError, ValueError):
                continue
        return None

    def _is_cross_margin_type(self, value: str | None) -> bool:
        return str(value or "").strip().lower() in {"cross", "crossed"}

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

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _to_float(value: Any, *, default: float = 0.0) -> float:
        try:
            if value is None:
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)
