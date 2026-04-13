from __future__ import annotations

from typing import Any

from meowbot.core.services.exchange.binance_api_key_validation_service import (
    BinanceApiKeyValidationService,
)
from meowbot.core.services.security.fernet_crypto_service import FernetCryptoService


class UserApiKeysService:
    def __init__(
        self,
        *,
        user_api_keys_repo,
        crypto_service: FernetCryptoService,
        validation_service: BinanceApiKeyValidationService,
    ) -> None:
        self.user_api_keys_repo = user_api_keys_repo
        self.crypto_service = crypto_service
        self.validation_service = validation_service

    async def save_binance_key_for_user(
        self,
        *,
        user_id,
        label: str,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
    ) -> dict[str, Any]:
        validation = await self.validation_service.validate(
            api_key=api_key,
            api_secret=api_secret,
        )

        encrypted_api_key = self.crypto_service.encrypt_text(api_key)
        encrypted_api_secret = self.crypto_service.encrypt_text(api_secret)
        encrypted_passphrase = (
            self.crypto_service.encrypt_text(passphrase) if passphrase else None
        )

        row = await self.user_api_keys_repo.upsert_user_api_key(
            user_id=user_id,
            exchange="binance",
            label=label,
            encrypted_api_key=encrypted_api_key,
            encrypted_api_secret=encrypted_api_secret,
            encrypted_passphrase=encrypted_passphrase,
            api_key_fingerprint=validation.api_key_fingerprint,
            exchange_account_fingerprint=validation.exchange_account_fingerprint,
            permissions_json=validation.permissions_json,
            validation_status="valid" if validation.ok else "invalid",
            is_active=validation.ok,
        )

        if validation.ok:
            await self.user_api_keys_repo.deactivate_other_keys_for_user(
                user_id=user_id,
                exchange="binance",
                except_key_id=row["id"],
            )

        return {
            "stored_key": row,
            "validation_ok": validation.ok,
            "validation_reason": validation.reason,
            "permissions_json": validation.permissions_json,
        }