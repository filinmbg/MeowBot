from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


_USD_QUANT = Decimal("0.01")


def _normalize_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if hasattr(raw, "items"):
        try:
            return dict(raw)
        except Exception:
            return {}
    return {}


@dataclass(frozen=True)
class ManualPaymentSettings:
    wallet_address: str
    wallet_label: str | None = None
    ttl_minutes: int = 20
    support_contact: str | None = None
    payment_method_code: str = "manual_usdt_trc20"
    provider_code: str = "manual_usdt"
    currency_code: str = "USDT"
    network_code: str = "TRC20"


class ManualSubscriptionPaymentsService:
    def __init__(
        self,
        *,
        users_repo,
        subscription_plans_repo,
        user_api_keys_repo,
        crypto_service,
        validation_service,
        settings: ManualPaymentSettings,
    ) -> None:
        self.users_repo = users_repo
        self.subscription_plans_repo = subscription_plans_repo
        self.user_api_keys_repo = user_api_keys_repo
        self.crypto_service = crypto_service
        self.validation_service = validation_service
        self.settings = settings

    async def get_purchase_context_by_telegram_id(
        self,
        *,
        telegram_id: int,
        plan_code: str,
    ) -> dict[str, Any]:
        user_row = _normalize_mapping(await self.users_repo.get_by_telegram_id(int(telegram_id)))
        if not user_row:
            return {"ok": False, "reason": "user_not_found"}

        plan_row = _normalize_mapping(await self.subscription_plans_repo.get_by_code(plan_code))
        if not plan_row:
            return {"ok": False, "reason": "plan_not_found", "user_row": user_row}

        return await self._evaluate_purchase_context(
            user_row=user_row,
            plan_row=plan_row,
        )

    async def revalidate_api_for_user_id(self, *, user_id) -> dict[str, Any]:
        api_row = _normalize_mapping(
            await self.user_api_keys_repo.get_latest_key_material_by_user_id(
                user_id=user_id,
                exchange="binance",
                require_active=False,
            )
        )
        if not api_row:
            return {"ok": False, "reason": "api_missing", "api_row": {}}

        encrypted_key = str(api_row.get("encrypted_api_key") or "").strip()
        encrypted_secret = str(api_row.get("encrypted_api_secret") or "").strip()
        if not encrypted_key or not encrypted_secret:
            return {"ok": False, "reason": "api_secret_missing", "api_row": api_row}

        try:
            api_key = self.crypto_service.decrypt_text(encrypted_key)
            api_secret = self.crypto_service.decrypt_text(encrypted_secret)
        except Exception:
            return {"ok": False, "reason": "api_decrypt_failed", "api_row": api_row}

        validation = await self.validation_service.validate(
            api_key=api_key,
            api_secret=api_secret,
        )
        permissions = dict(validation.permissions_json or {})
        await self.user_api_keys_repo.update_validation_result(
            key_id=api_row["id"],
            permissions_json=permissions,
            validation_status="valid" if validation.ok else "invalid",
            is_active=validation.ok,
            exchange_account_fingerprint=validation.exchange_account_fingerprint,
        )

        return {
            "ok": bool(validation.ok),
            "reason": validation.reason,
            "permissions_json": permissions,
            "api_row": api_row,
            "api_key_fingerprint": validation.api_key_fingerprint,
            "exchange_account_fingerprint": validation.exchange_account_fingerprint,
        }

    async def _evaluate_purchase_context(
        self,
        *,
        user_row: dict[str, Any],
        plan_row: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(user_row.get("status") or "").lower()
        if status != "active":
            return {
                "ok": False,
                "reason": "user_blocked" if status == "blocked" else "user_inactive",
                "user_row": user_row,
                "plan_row": plan_row,
            }

        if not bool(plan_row.get("is_active")):
            return {"ok": False, "reason": "plan_inactive", "user_row": user_row, "plan_row": plan_row}

        features = _normalize_mapping(plan_row.get("features_json"))
        live_enabled = bool(features.get("live_enabled", False))
        price_value = self.money(plan_row.get("price_usd", 0))
        if price_value <= Decimal("0.00"):
            return {"ok": False, "reason": "plan_not_paid", "user_row": user_row, "plan_row": plan_row}
        if not live_enabled:
            return {"ok": False, "reason": "plan_live_not_available", "user_row": user_row, "plan_row": plan_row}

        api_row = _normalize_mapping(
            await self.user_api_keys_repo.get_latest_key_material_by_user_id(
                user_id=user_row["id"],
                exchange="binance",
                require_active=False,
            )
        )
        if not api_row:
            return {
                "ok": False,
                "reason": "api_missing",
                "user_row": user_row,
                "plan_row": plan_row,
                "api_row": {},
            }

        encrypted_key = str(api_row.get("encrypted_api_key") or "").strip()
        encrypted_secret = str(api_row.get("encrypted_api_secret") or "").strip()
        if not encrypted_key or not encrypted_secret:
            return {
                "ok": False,
                "reason": "api_secret_missing",
                "user_row": user_row,
                "plan_row": plan_row,
                "api_row": api_row,
            }

        validation_status = str(api_row.get("validation_status") or "").lower()
        if validation_status != "valid":
            return {
                "ok": False,
                "reason": "api_invalid",
                "user_row": user_row,
                "plan_row": plan_row,
                "api_row": api_row,
            }

        permissions = _normalize_mapping(api_row.get("permissions_json"))
        if not bool(permissions.get("enableFutures", False)):
            return {
                "ok": False,
                "reason": "api_futures_disabled",
                "user_row": user_row,
                "plan_row": plan_row,
                "api_row": api_row,
                "permissions_json": permissions,
            }

        return {
            "ok": True,
            "reason": "ok",
            "user_row": user_row,
            "plan_row": plan_row,
            "api_row": api_row,
            "permissions_json": permissions,
            "features_json": features,
        }

    @staticmethod
    def money(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value.quantize(_USD_QUANT, rounding=ROUND_HALF_UP)
        return Decimal(str(value or 0)).quantize(_USD_QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_discount(
        *,
        base_amount: Decimal,
        discount_percent: Decimal | int | float | None,
    ) -> tuple[Decimal, Decimal]:
        percent = Decimal(str(discount_percent or 0)).quantize(_USD_QUANT, rounding=ROUND_HALF_UP)
        if percent < Decimal("0"):
            percent = Decimal("0")
        if percent > Decimal("100"):
            percent = Decimal("100")
        discount_amount = (
            (base_amount * percent / Decimal("100")).quantize(_USD_QUANT, rounding=ROUND_HALF_UP)
        )
        final_amount = (base_amount - discount_amount).quantize(_USD_QUANT, rounding=ROUND_HALF_UP)
        if final_amount < Decimal("0"):
            final_amount = Decimal("0.00")
        return discount_amount, final_amount

    @staticmethod
    def normalize_tx_hash(value: str | None) -> str | None:
        tx_hash = str(value or "").strip()
        if not tx_hash:
            return None
        if len(tx_hash) < 16:
            return None
        return tx_hash
