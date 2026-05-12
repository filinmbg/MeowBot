from __future__ import annotations


class SubmitManualPaymentTxHashUseCase:
    def __init__(
        self,
        *,
        purchase_intents_repo,
        payments_repo,
        manual_payment_submissions_repo,
        manual_payments_service,
    ) -> None:
        self.purchase_intents_repo = purchase_intents_repo
        self.payments_repo = payments_repo
        self.manual_payment_submissions_repo = manual_payment_submissions_repo
        self.manual_payments_service = manual_payments_service

    async def execute(
        self,
        *,
        user_id,
        purchase_intent_id,
        tx_hash: str,
        submitted_amount=None,
        submitted_network: str | None = None,
    ) -> dict:
        await self.purchase_intents_repo.expire_stale()
        intent_row = await self.purchase_intents_repo.get_by_id_for_user(
            intent_id=purchase_intent_id,
            user_id=user_id,
        )
        if not intent_row:
            return {"ok": False, "reason": "intent_not_found"}
        if intent_row.get("status") == "expired":
            await self.payments_repo.expire_by_intent(purchase_intent_id=purchase_intent_id)
            return {"ok": False, "reason": "intent_expired", "intent_row": intent_row}
        if intent_row.get("status") == "awaiting_manual_check":
            existing_submission = await self.manual_payment_submissions_repo.get_by_purchase_intent_id(
                purchase_intent_id=purchase_intent_id
            )
            return {
                "ok": True,
                "reason": "already_submitted",
                "intent_row": intent_row,
                "submission_row": existing_submission,
            }
        if intent_row.get("status") != "awaiting_payment":
            return {"ok": False, "reason": "intent_not_submittable", "intent_row": intent_row}

        normalized_tx_hash = self.manual_payments_service.normalize_tx_hash(tx_hash)
        if not normalized_tx_hash:
            return {"ok": False, "reason": "tx_hash_invalid", "intent_row": intent_row}

        payment_row = await self.payments_repo.get_by_purchase_intent_id(
            purchase_intent_id=purchase_intent_id
        )
        if not payment_row:
            return {"ok": False, "reason": "payment_not_found", "intent_row": intent_row}

        await self.payments_repo.update_submission(
            payment_id=payment_row["id"],
            tx_hash=normalized_tx_hash,
            submitted_amount=submitted_amount,
            submitted_network=submitted_network,
            raw_payload_json={"submitted_tx_hash": normalized_tx_hash},
        )
        submission_row = await self.manual_payment_submissions_repo.create_or_get(
            purchase_intent_id=purchase_intent_id,
            payment_id=payment_row["id"],
            user_id=user_id,
            submitted_tx_hash=normalized_tx_hash,
            submitted_amount=submitted_amount,
            submitted_network=submitted_network,
        )
        await self.purchase_intents_repo.update_status(
            intent_id=purchase_intent_id,
            status="awaiting_manual_check",
        )
        updated_intent = await self.purchase_intents_repo.get_by_id(purchase_intent_id)
        updated_payment = await self.payments_repo.get_by_purchase_intent_id(
            purchase_intent_id=purchase_intent_id
        )
        return {
            "ok": True,
            "reason": "submitted",
            "intent_row": updated_intent,
            "payment_row": updated_payment,
            "submission_row": submission_row,
        }
