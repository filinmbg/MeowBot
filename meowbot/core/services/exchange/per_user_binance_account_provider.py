from __future__ import annotations

from typing import Any

from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsync,
    BinanceFuturesAccountAsyncConfig,
)


class PerUserBinanceAccountProvider:
    def __init__(
        self,
        *,
        user_api_keys_repo,
        crypto_service,
        account_config: BinanceFuturesAccountAsyncConfig | None = None,
    ) -> None:
        self.user_api_keys_repo = user_api_keys_repo
        self.crypto_service = crypto_service
        self.account_config = account_config or BinanceFuturesAccountAsyncConfig()

    async def build_client(
        self,
        *,
        runtime_user_id: str,
        require_active: bool = True,
    ) -> tuple[BinanceFuturesAccountAsync | None, dict[str, Any] | None, str | None]:
        row = await self.user_api_keys_repo.get_latest_key_material_by_runtime_user_id(
            runtime_user_id=runtime_user_id,
            exchange="binance",
            require_active=require_active,
        )
        if not row:
            return None, None, "api_key_not_found"

        encrypted_api_key = str(row.get("encrypted_api_key") or "")
        encrypted_api_secret = str(row.get("encrypted_api_secret") or "")
        if not encrypted_api_key or not encrypted_api_secret:
            return None, row, "api_key_payload_incomplete"

        try:
            api_key = self.crypto_service.decrypt_text(encrypted_api_key)
            api_secret = self.crypto_service.decrypt_text(encrypted_api_secret)
        except Exception:
            return None, row, "api_key_decryption_failed"

        client = BinanceFuturesAccountAsync(
            api_key=api_key,
            api_secret=api_secret,
            config=self.account_config,
        )
        return client, row, None
