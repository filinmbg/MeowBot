from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class ApplyPromoCodeToPurchaseUseCase:
    def __init__(
        self,
        *,
        manual_payments_service,
        promo_codes_repo,
        promo_code_redemptions_repo,
    ) -> None:
        self.manual_payments_service = manual_payments_service
        self.promo_codes_repo = promo_codes_repo
        self.promo_code_redemptions_repo = promo_code_redemptions_repo

    async def execute(
        self,
        *,
        user_id,
        plan_row: dict[str, Any],
        promo_code: str,
    ) -> dict[str, Any]:
        code = str(promo_code or "").strip()
        if not code:
            return {"ok": False, "reason": "promo_empty"}

        promo_row = await self.promo_codes_repo.get_by_code(code)
        if not promo_row:
            return {"ok": False, "reason": "promo_not_found"}

        if not bool(promo_row.get("is_active")):
            return {"ok": False, "reason": "promo_inactive", "promo_row": promo_row}

        now = datetime.now(timezone.utc)
        active_from = promo_row.get("active_from")
        active_to = promo_row.get("active_to")
        if active_from and active_from > now:
            return {"ok": False, "reason": "promo_not_started", "promo_row": promo_row}
        if active_to and active_to < now:
            return {"ok": False, "reason": "promo_expired", "promo_row": promo_row}

        max_redemptions = int(promo_row.get("max_redemptions") or 0)
        used_redemptions = int(promo_row.get("used_redemptions") or 0)
        if max_redemptions > 0 and used_redemptions >= max_redemptions:
            return {"ok": False, "reason": "promo_limit_reached", "promo_row": promo_row}

        applies_to_all = bool(promo_row.get("applies_to_all_paid_plans"))
        specific_plan_id = promo_row.get("specific_plan_id")
        if not applies_to_all and specific_plan_id != plan_row.get("id"):
            return {"ok": False, "reason": "promo_plan_mismatch", "promo_row": promo_row}

        existing_redemption = await self.promo_code_redemptions_repo.get_by_promo_and_user(
            promo_code_id=promo_row["id"],
            user_id=user_id,
        )
        if existing_redemption:
            return {"ok": False, "reason": "promo_already_used", "promo_row": promo_row}

        base_amount = self.manual_payments_service.money(plan_row.get("price_usd", 0))
        discount_percent = Decimal(str(promo_row.get("discount_percent") or 0))
        discount_amount, final_amount = self.manual_payments_service.calculate_discount(
            base_amount=base_amount,
            discount_percent=discount_percent,
        )
        return {
            "ok": True,
            "reason": "ok",
            "promo_row": promo_row,
            "discount_percent": discount_percent,
            "discount_amount": discount_amount,
            "final_amount": final_amount,
            "base_amount": base_amount,
        }
