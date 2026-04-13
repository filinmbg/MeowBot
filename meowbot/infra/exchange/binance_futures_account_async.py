from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from meowbot.infra.exchange.binance_request_gate import (
    BinanceGateConfig,
    BinanceRequestGate,
)


log = logging.getLogger("meowbot")


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

    recv_window_ms: int = 5000


class BinanceFuturesAccountAsync:
    """
    Signed Binance client for:
    - Futures account info (/fapi/*)
    - Wallet API key permissions (/sapi/*)

    Окремо від market-data client.
    """

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

    async def close(self) -> None:
        await self._futures_client.aclose()
        await self._wallet_client.aclose()

    async def get_account_info(self) -> dict[str, Any] | None:
        try:
            payload = await self._signed_request_json(
                client=self._futures_client,
                gate=self._futures_gate,
                path="/fapi/v3/account",
                params={},
                weight=5,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning(
                "[binance-account] get_account_info failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return None

    async def get_api_key_permissions(self) -> dict[str, Any] | None:
        try:
            payload = await self._signed_request_json(
                client=self._wallet_client,
                gate=self._wallet_gate,
                path="/sapi/v1/account/apiRestrictions",
                params={},
                weight=1,
            )
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning(
                "[binance-account] get_api_key_permissions failed: %s: %s",
                type(exc).__name__,
                exc,
            )
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

    async def _signed_request_json(
        self,
        *,
        client: httpx.AsyncClient,
        gate: BinanceRequestGate,
        path: str,
        params: dict[str, Any],
        weight: int,
    ) -> dict | list:
        signed_params = dict(params)
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = self.config.recv_window_ms

        query_string = urlencode(signed_params, doseq=True)
        signature = hmac.new(
            self.api_secret,
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        signed_params["signature"] = signature

        return await gate.execute_json(
            client=client,
            method="GET",
            path=path,
            params=signed_params,
            weight=weight,
        )