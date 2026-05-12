from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class CreateManualPurchaseIntentUseCase:
    def __init__(
        self,
        *,
        purchase_intents_repo,
        payments_repo,
        manual_payments_service,
        apply_promo_code_usecase,
    ) -> None:
        self.purchase_intents_repo = purchase_intents_repo
        self.payments_repo = payments_repo
        self.manual_payments_service = manual_payments_service
        self.apply_promo_code_usecase = apply_promo_code_usecase

    async def execute(
        self,
        *,
        telegram_id: int,
        plan_code: str,
        promo_code: str | None = None,
    ) -> dict[str, Any]:
        if not str(self.manual_payments_service.settings.wallet_address or "").strip():
            return {"ok": False, "reason": "wallet_not_configured"}
        context = await self.manual_payments_service.get_purchase_context_by_telegram_id(
            telegram_id=telegram_id,
            plan_code=plan_code,
        )
        if not context.get("ok"):
            return context

        user_row = context["user_row"]
        plan_row = context["plan_row"]

        await self.purchase_intents_repo.expire_stale()
        existing_intent = await self.purchase_intents_repo.get_latest_open_by_user_and_plan(
            user_id=user_row["id"],
            plan_code=plan_row["code"],
        )
        if existing_intent and existing_intent.get("status") == "awaiting_payment":
            existing_payment = await self.payments_repo.get_by_purchase_intent_id(
                purchase_intent_id=existing_intent["id"]
            )
            return {
                "ok": True,
                "reason": "existing_awaiting_payment",
                "intent_row": existing_intent,
                "payment_row": existing_payment,
                "plan_row": plan_row,
                "user_row": user_row,
            }
        if existing_intent and existing_intent.get("status") == "awaiting_manual_check":
            existing_payment = await self.payments_repo.get_by_purchase_intent_id(
                purchase_intent_id=existing_intent["id"]
            )
            return {
                "ok": True,
                "reason": "existing_under_review",
                "intent_row": existing_intent,
                "payment_row": existing_payment,
                "plan_row": plan_row,
                "user_row": user_row,
            }

        base_amount = self.manual_payments_service.money(plan_row.get("price_usd", 0))
        promo_row = None
        discount_percent = None
        discount_amount = self.manual_payments_service.money(0)
        final_amount = base_amount
        if promo_code:
            promo_result = await self.apply_promo_code_usecase.execute(
                user_id=user_row["id"],
                plan_row=plan_row,
                promo_code=promo_code,
            )
            if not promo_result.get("ok"):
                return promo_result | {"user_row": user_row, "plan_row": plan_row}
            promo_row = promo_result["promo_row"]
            discount_percent = promo_result["discount_percent"]
            discount_amount = promo_result["discount_amount"]
            final_amount = promo_result["final_amount"]

        order_code = self._build_order_code()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=int(self.manual_payments_service.settings.ttl_minutes)
        )
        intent_row = await self.purchase_intents_repo.create(
            order_code=order_code,
            user_id=user_row["id"],
            plan_id=plan_row["id"],
            plan_code=plan_row["code"],
            payment_method_code=self.manual_payments_service.settings.payment_method_code,
            base_amount_usd=base_amount,
            discount_percent=discount_percent,
            discount_amount_usd=discount_amount,
            final_amount_usd=final_amount,
            promo_code_id=(promo_row or {}).get("id"),
            status="awaiting_payment",
            expires_at=expires_at,
            metadata_json={"telegram_id": int(telegram_id), "source": "telegram_manual_payment"},
        )
        payment_row = await self.payments_repo.create_pending(
            user_id=user_row["id"],
            purchase_intent_id=intent_row["id"],
            provider="manual",
            provider_code=self.manual_payments_service.settings.provider_code,
            currency_code=self.manual_payments_service.settings.currency_code,
            network_code=self.manual_payments_service.settings.network_code,
            expected_amount=final_amount,
            wallet_address=self.manual_payments_service.settings.wallet_address,
            payment_method_code=self.manual_payments_service.settings.payment_method_code,
            raw_payload_json={"order_code": order_code},
        )
        return {
            "ok": True,
            "reason": "created",
            "intent_row": intent_row,
            "payment_row": payment_row,
            "plan_row": plan_row,
            "user_row": user_row,
            "promo_row": promo_row,
        }

    @staticmethod
    def _build_order_code() -> str:
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        suffix = uuid.uuid4().hex[:4].upper()
        return f"MB-{date_part}-{suffix}"
