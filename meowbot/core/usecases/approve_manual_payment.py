from __future__ import annotations

from datetime import datetime, timedelta, timezone


class ApproveManualPaymentUseCase:
    def __init__(
        self,
        *,
        pool,
        manual_payments_service,
        purchase_intents_repo,
        payments_repo,
        manual_payment_submissions_repo,
        promo_code_redemptions_repo,
        promo_codes_repo,
        admin_audit_logs_repo,
        trades_repo=None,
    ) -> None:
        self.pool = pool
        self.manual_payments_service = manual_payments_service
        self.purchase_intents_repo = purchase_intents_repo
        self.payments_repo = payments_repo
        self.manual_payment_submissions_repo = manual_payment_submissions_repo
        self.promo_code_redemptions_repo = promo_code_redemptions_repo
        self.promo_codes_repo = promo_codes_repo
        self.admin_audit_logs_repo = admin_audit_logs_repo
        self.trades_repo = trades_repo

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
                        pi.discount_percent,
                        pi.discount_amount_usd,
                        pi.promo_code_id,
                        pi.status as purchase_status,
                        p.status as payment_status,
                        p.paid_amount,
                        sp.name as plan_name,
                        sp.duration_days,
                        u.display_name,
                        u.email,
                        u.preferred_language,
                        tp.telegram_id,
                        tp.chat_id,
                        tp.username
                    from manual_payment_submissions mps
                    join purchase_intents pi
                        on pi.id = mps.purchase_intent_id
                    join payments p
                        on p.id = mps.payment_id
                    join subscription_plans sp
                        on sp.id = pi.plan_id
                    join users u
                        on u.id = mps.user_id
                    left join telegram_profiles tp
                        on tp.user_id = u.id
                    where mps.id = $1
                    for update of mps, pi, p
                    """,
                    submission_id,
                )
                if not review_row:
                    return {"ok": False, "reason": "submission_not_found"}

                data = dict(review_row)
                if data["submission_status"] == "approved":
                    return {"ok": True, "reason": "already_approved", "review_row": data}
                if data["submission_status"] == "rejected":
                    return {"ok": False, "reason": "already_rejected", "review_row": data}
                if data["purchase_status"] == "expired":
                    return {"ok": False, "reason": "intent_expired", "review_row": data}
                if data["purchase_status"] != "awaiting_manual_check":
                    return {"ok": False, "reason": "intent_not_reviewable", "review_row": data}

                api_validation = await self.manual_payments_service.revalidate_api_for_user_id(
                    user_id=data["user_id"]
                )
                subscription_status = "active" if api_validation.get("ok") else "requires_api_fix"
                starts_at = datetime.now(timezone.utc) if subscription_status == "active" else None
                ends_at = (
                    datetime.now(timezone.utc) + timedelta(days=int(data["duration_days"] or 0))
                    if subscription_status == "active"
                    else None
                )

                await conn.execute(
                    """
                    update user_subscriptions
                    set
                        status = case when coalesce(is_trial, false) then 'expired' else 'cancelled' end,
                        cancelled_at = case when coalesce(is_trial, false) then cancelled_at else now() end,
                        ended_reason = case
                            when coalesce(is_trial, false) then 'converted_to_paid_manual'
                            else 'replaced_by_paid_manual'
                        end,
                        updated_at = now()
                    where user_id = $1
                      and status in ('active', 'requires_api_fix')
                    """,
                    data["user_id"],
                )
                await conn.execute(
                    """
                    update trial_consumptions
                    set
                        status = 'converted',
                        ended_at = now()
                    where user_id = $1
                      and status = 'started'
                    """,
                    data["user_id"],
                )

                subscription_row = await conn.fetchrow(
                    """
                    insert into user_subscriptions (
                        user_id,
                        plan_id,
                        status,
                        starts_at,
                        ends_at,
                        auto_renew,
                        cancelled_at,
                        source,
                        is_trial,
                        trial_code,
                        ended_reason,
                        strategy_version
                    )
                    values (
                        $1, $2, $3, $4, $5,
                        false, null, 'manual_payment',
                        false, null, null, 'v2'
                    )
                    returning
                        id,
                        user_id,
                        plan_id,
                        status,
                        starts_at,
                        ends_at,
                        auto_renew,
                        cancelled_at,
                        source,
                        is_trial,
                        strategy_version,
                        trial_code,
                        ended_reason,
                        created_at,
                        updated_at
                    """,
                    data["user_id"],
                    data["plan_id"],
                    subscription_status,
                    starts_at,
                    ends_at,
                )

                if subscription_status == "active":
                    await conn.execute(
                        """
                        update trader_settings
                        set
                            trading_enabled = true,
                            trading_mode = 'live',
                            updated_at = now()
                        where user_id = $1
                        """,
                        data["user_id"],
                    )

                if data.get("promo_code_id"):
                    redemption_row = await self.promo_code_redemptions_repo.get_by_purchase_intent_id(
                        purchase_intent_id=data["purchase_intent_id"],
                        conn=conn,
                    )
                    if redemption_row is None:
                        await self.promo_code_redemptions_repo.create(
                            promo_code_id=data["promo_code_id"],
                            user_id=data["user_id"],
                            purchase_intent_id=data["purchase_intent_id"],
                            plan_id=data["plan_id"],
                            discount_percent=data.get("discount_percent"),
                            discount_amount=data.get("discount_amount_usd"),
                            conn=conn,
                        )
                        await self.promo_codes_repo.increment_used_redemptions(
                            promo_id=data["promo_code_id"],
                            conn=conn,
                        )

                intent_row = await self.purchase_intents_repo.update_status(
                    intent_id=data["purchase_intent_id"],
                    status="approved",
                    conn=conn,
                )
                payment_row = await self.payments_repo.update_status(
                    payment_id=data["payment_id"],
                    status="succeeded",
                    paid_amount=data["final_amount_usd"],
                    comment=admin_comment,
                    conn=conn,
                )
                submission_row = await self.manual_payment_submissions_repo.review(
                    submission_id=data["submission_id"],
                    status="approved",
                    admin_user_id=admin_user_id,
                    admin_comment=admin_comment,
                    conn=conn,
                )
                await self.admin_audit_logs_repo.create(
                    actor_user_id=admin_user_id,
                    target_user_id=data["user_id"],
                    action="payment_approved",
                    entity_type="manual_payment_submission",
                    entity_id=str(data["submission_id"]),
                    details_json={
                        "order_code": data["order_code"],
                        "plan_code": data["plan_code"],
                        "subscription_status": subscription_status,
                        "strategy_version": "v2",
                        "tx_hash": data["submitted_tx_hash"],
                    },
                    conn=conn,
                )

        closed_sandbox_trades = 0
        if subscription_status == "active" and self.trades_repo is not None and data.get("telegram_id"):
            closed_sandbox_trades = await self.trades_repo.close_open_trades_by_user_mode(
                user_id=f"tg:{data['telegram_id']}",
                mode="sandbox",
                closed_at=int(datetime.now(tz=timezone.utc).timestamp() * 1000),
                reason="SWITCH_TO_LIVE",
            )

        return {
            "ok": True,
            "reason": "approved",
            "review_row": data,
            "intent_row": dict(intent_row) if intent_row else None,
            "payment_row": dict(payment_row) if payment_row else None,
            "submission_row": dict(submission_row) if submission_row else None,
            "subscription_row": dict(subscription_row),
            "requires_api_fix": subscription_status == "requires_api_fix",
            "closed_sandbox_trades": closed_sandbox_trades,
        }
