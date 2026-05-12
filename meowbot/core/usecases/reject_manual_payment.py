from __future__ import annotations


class RejectManualPaymentUseCase:
    def __init__(
        self,
        *,
        pool,
        purchase_intents_repo,
        payments_repo,
        manual_payment_submissions_repo,
        admin_audit_logs_repo,
    ) -> None:
        self.pool = pool
        self.purchase_intents_repo = purchase_intents_repo
        self.payments_repo = payments_repo
        self.manual_payment_submissions_repo = manual_payment_submissions_repo
        self.admin_audit_logs_repo = admin_audit_logs_repo

    async def execute(
        self,
        *,
        submission_id,
        admin_user_id,
        admin_comment: str | None = None,
    ) -> dict:
        await self.purchase_intents_repo.expire_stale()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                review_row = await conn.fetchrow(
                    """
                    select
                        mps.id as submission_id,
                        mps.purchase_intent_id,
                        mps.payment_id,
                        mps.user_id,
                        mps.status as submission_status,
                        mps.submitted_tx_hash,
                        pi.order_code,
                        pi.plan_id,
                        pi.plan_code,
                        pi.final_amount_usd,
                        pi.status as purchase_status,
                        sp.name as plan_name,
                        u.display_name,
                        u.email,
                        u.preferred_language,
                        tp.telegram_id,
                        tp.chat_id,
                        tp.username
                    from manual_payment_submissions mps
                    join purchase_intents pi
                        on pi.id = mps.purchase_intent_id
                    join subscription_plans sp
                        on sp.id = pi.plan_id
                    join users u
                        on u.id = mps.user_id
                    left join telegram_profiles tp
                        on tp.user_id = u.id
                    where mps.id = $1
                    for update of mps, pi
                    """,
                    submission_id,
                )
                if not review_row:
                    return {"ok": False, "reason": "submission_not_found"}

                data = dict(review_row)
                if data["submission_status"] == "rejected":
                    return {"ok": True, "reason": "already_rejected", "review_row": data}
                if data["submission_status"] == "approved":
                    return {"ok": False, "reason": "already_approved", "review_row": data}

                intent_row = await self.purchase_intents_repo.update_status(
                    intent_id=data["purchase_intent_id"],
                    status="rejected",
                    conn=conn,
                )
                payment_row = await self.payments_repo.update_status(
                    payment_id=data["payment_id"],
                    status="rejected",
                    comment=admin_comment,
                    conn=conn,
                )
                submission_row = await self.manual_payment_submissions_repo.review(
                    submission_id=data["submission_id"],
                    status="rejected",
                    admin_user_id=admin_user_id,
                    admin_comment=admin_comment,
                    conn=conn,
                )
                await self.admin_audit_logs_repo.create(
                    actor_user_id=admin_user_id,
                    target_user_id=data["user_id"],
                    action="payment_rejected",
                    entity_type="manual_payment_submission",
                    entity_id=str(data["submission_id"]),
                    details_json={
                        "order_code": data["order_code"],
                        "plan_code": data["plan_code"],
                        "tx_hash": data["submitted_tx_hash"],
                        "comment": admin_comment,
                    },
                    conn=conn,
                )

        return {
            "ok": True,
            "reason": "rejected",
            "review_row": data,
            "intent_row": dict(intent_row) if intent_row else None,
            "payment_row": dict(payment_row) if payment_row else None,
            "submission_row": dict(submission_row) if submission_row else None,
        }
