from __future__ import annotations

from meowbot.core.services.risk.live_account_risk_service import (
    LiveEntryRiskResult,
    LiveAccountRiskService,
)
from meowbot.infra.exchange.binance_futures_account_async import (
    BinanceFuturesAccountAsync,
    BinanceFuturesAccountAsyncConfig,
)


class PerUserLiveRiskService:
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

    async def check_new_entry(
        self,
        *,
        runtime_user_id: str,
        symbol: str,
        entry_price: float,
        default_stake_mode: str,
        default_stake_value: float,
        min_entry_margin_usdt: float,
        default_leverage: int,
        max_margin_per_trade_mode: str,
        max_margin_per_trade_value: float,
        margin_ratio_warn_pct: float,
        margin_ratio_block_pct: float,
        position_size_multiplier: float = 1.0,
    ) -> LiveEntryRiskResult:
        key_row = await self.user_api_keys_repo.get_active_key_by_runtime_user_id(
            runtime_user_id=runtime_user_id,
            exchange="binance",
        )
        if not key_row:
            return LiveEntryRiskResult(
                allowed=False,
                reason="active_api_key_not_found",
            )

        encrypted_api_key = str(key_row.get("encrypted_api_key") or "")
        encrypted_api_secret = str(key_row.get("encrypted_api_secret") or "")

        if not encrypted_api_key or not encrypted_api_secret:
            return LiveEntryRiskResult(
                allowed=False,
                reason="api_key_payload_incomplete",
            )

        try:
            api_key = self.crypto_service.decrypt_text(encrypted_api_key)
            api_secret = self.crypto_service.decrypt_text(encrypted_api_secret)
        except Exception:
            return LiveEntryRiskResult(
                allowed=False,
                reason="api_key_decryption_failed",
            )

        client = BinanceFuturesAccountAsync(
            api_key=api_key,
            api_secret=api_secret,
            config=self.account_config,
        )

        try:
            risk_service = LiveAccountRiskService(
                account_info_provider=client.get_account_info,
                symbol_max_leverage_provider=client.get_symbol_max_leverage,
                symbol_trading_rules_provider=client.get_symbol_trading_rules,
                margin_buffer_ratio=0.98,
            )

            return await risk_service.check_new_entry(
                symbol=symbol,
                entry_price=entry_price,
                default_stake_mode=default_stake_mode,
                default_stake_value=default_stake_value,
                min_entry_margin_usdt=min_entry_margin_usdt,
                default_leverage=default_leverage,
                max_margin_per_trade_mode=max_margin_per_trade_mode,
                max_margin_per_trade_value=max_margin_per_trade_value,
                margin_ratio_warn_pct=margin_ratio_warn_pct,
                margin_ratio_block_pct=margin_ratio_block_pct,
                position_size_multiplier=position_size_multiplier,
            )
        finally:
            await client.close()
