from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsync,
    BinanceFuturesAccountAsyncConfig,
)


@dataclass(frozen=True)
class ApiKeyValidationResult:
    ok: bool
    reason: str
    permissions_json: dict[str, Any]
    api_key_fingerprint: str
    exchange_account_fingerprint: str | None


class BinanceApiKeyValidationService:
    def __init__(
        self,
        *,
        account_config: BinanceFuturesAccountAsyncConfig | None = None,
        require_reading: bool = True,
        require_futures: bool = True,
        require_withdrawals_disabled: bool = True,
    ) -> None:
        self.account_config = account_config or BinanceFuturesAccountAsyncConfig()
        self.require_reading = require_reading
        self.require_futures = require_futures
        self.require_withdrawals_disabled = require_withdrawals_disabled

    async def validate(
        self,
        *,
        api_key: str,
        api_secret: str,
    ) -> ApiKeyValidationResult:
        client = BinanceFuturesAccountAsync(
            api_key=api_key,
            api_secret=api_secret,
            config=self.account_config,
        )

        try:
            permissions = await client.get_api_key_permissions()
            if not permissions:
                return ApiKeyValidationResult(
                    ok=False,
                    reason="permissions_unavailable",
                    permissions_json={},
                    api_key_fingerprint=self._fingerprint_api_key(api_key),
                    exchange_account_fingerprint=None,
                )

            account = await client.get_account_info()
            if not account:
                return ApiKeyValidationResult(
                    ok=False,
                    reason="futures_account_unavailable",
                    permissions_json=permissions,
                    api_key_fingerprint=self._fingerprint_api_key(api_key),
                    exchange_account_fingerprint=None,
                )

            enable_reading = bool(permissions.get("enableReading", False))
            enable_withdrawals = bool(permissions.get("enableWithdrawals", False))
            enable_futures = bool(permissions.get("enableFutures", False))

            if self.require_reading and not enable_reading:
                return ApiKeyValidationResult(
                    ok=False,
                    reason="enableReading_required",
                    permissions_json=permissions,
                    api_key_fingerprint=self._fingerprint_api_key(api_key),
                    exchange_account_fingerprint=self._fingerprint_account(account),
                )

            if self.require_futures and not enable_futures:
                return ApiKeyValidationResult(
                    ok=False,
                    reason="enableFutures_required",
                    permissions_json=permissions,
                    api_key_fingerprint=self._fingerprint_api_key(api_key),
                    exchange_account_fingerprint=self._fingerprint_account(account),
                )

            if self.require_withdrawals_disabled and enable_withdrawals:
                permissions = dict(permissions)
                permissions["policy_warning"] = "enableWithdrawals_enabled"
                return ApiKeyValidationResult(
                    ok=True,
                    reason="enableWithdrawals_warning",
                    permissions_json=permissions,
                    api_key_fingerprint=self._fingerprint_api_key(api_key),
                    exchange_account_fingerprint=self._fingerprint_account(account),
                )

            return ApiKeyValidationResult(
                ok=True,
                reason="ok",
                permissions_json=permissions,
                api_key_fingerprint=self._fingerprint_api_key(api_key),
                exchange_account_fingerprint=self._fingerprint_account(account),
            )
        finally:
            await client.close()

    @staticmethod
    def _fingerprint_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint_account(account_info: dict[str, Any]) -> str | None:
        assets = account_info.get("assets")
        positions = account_info.get("positions")

        raw = {
            "canTrade": account_info.get("canTrade"),
            "multiAssetsMargin": account_info.get("multiAssetsMargin"),
            "totalWalletBalance": account_info.get("totalWalletBalance"),
            "assets_len": len(assets) if isinstance(assets, list) else None,
            "positions_len": len(positions) if isinstance(positions, list) else None,
        }

        try:
            material = repr(raw).encode("utf-8")
            return hashlib.sha256(material).hexdigest()
        except Exception:
            return None
