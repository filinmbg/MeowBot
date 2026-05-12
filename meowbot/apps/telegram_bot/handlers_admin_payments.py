from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meowbot.apps.telegram_bot.keyboards.payments_menu import (
    build_admin_payments_menu_keyboard,
    build_admin_pending_list_keyboard,
    build_admin_promos_list_keyboard,
    build_admin_promo_menu_keyboard,
    build_admin_purchases_keyboard,
    build_admin_review_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    format_dt,
    is_admin_user,
    safe_callback_answer,
    safe_edit_message,
)


router = Router()


class AdminPromoStates(StatesGroup):
    awaiting_payload = State()


async def _admin_context(user, *, i18n_service, users_repo, admin_ids):
    row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    if not is_admin_user(row, user.id, admin_ids):
        return None, lang
    return row, lang


async def _ensure_admin(target, *, i18n_service, users_repo, admin_ids):
    user = target.from_user
    if user is None:
        return None
    row, lang = await _admin_context(user, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if row is None:
        if isinstance(target, CallbackQuery):
            await safe_callback_answer(target, text=i18n_service.t(lang, "common.access_denied"), show_alert=True)
        else:
            await target.answer(i18n_service.t(lang, "common.access_denied"))
        return None
    return row, lang


async def render_admin_payments_menu(target, *, i18n_service, users_repo, admin_ids, **_) -> None:
    context = await _ensure_admin(target, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    text = f"{t('admin.manual_payments_title')}\n\n{t('common.placeholder')}"
    markup = build_admin_payments_menu_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


async def render_pending_submissions(
    callback: CallbackQuery,
    *,
    i18n_service,
    users_repo,
    admin_ids,
    manual_payment_submissions_repo,
    **_,
) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await manual_payment_submissions_repo.list_pending(limit=20)
    text_lines = [t("admin.pending_submissions_title"), ""]
    if not rows:
        text_lines.append(t("admin.no_pending_submissions"))
    else:
        for row in rows:
            text_lines.append(
                f"• <b>{row.get('order_code', '-')}</b> | {row.get('plan_code', '-')}\n"
                f"  {row.get('display_name') or row.get('username') or row.get('email') or '-'}"
            )
    await safe_edit_message(
        callback,
        text="\n".join(text_lines),
        reply_markup=build_admin_pending_list_keyboard(t, rows),
    )


async def render_submission_detail(
    callback: CallbackQuery,
    *,
    submission_id: str,
    i18n_service,
    users_repo,
    admin_ids,
    manual_payment_submissions_repo,
    **_,
) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    row = await manual_payment_submissions_repo.get_by_id(submission_id)
    if not row:
        await safe_edit_message(
            callback,
            text=t("admin.submission_not_found"),
            reply_markup=build_admin_payments_menu_keyboard(t),
        )
        return
    text = "\n".join(
        [
            t("admin.review_submission_title"),
            "",
            f"<b>{t('payment.order_code')}:</b> <code>{row.get('order_code', '-')}</code>",
            f"<b>{t('payment.plan')}:</b> {row.get('plan_name', '-')} ({row.get('plan_code', '-')})",
            f"<b>{t('payment.amount')}:</b> {float(row.get('final_amount_usd', 0) or 0):,.2f} USDT",
            f"<b>{t('admin.user_label')}:</b> {row.get('display_name') or row.get('username') or row.get('email') or '-'}",
            f"<b>{t('admin.telegram_label')}:</b> {row.get('telegram_id', '-')}",
            f"<b>{t('payment.tx_hash')}:</b> <code>{row.get('submitted_tx_hash', '-')}</code>",
            f"<b>{t('admin.created_at')}:</b> {format_dt(row.get('created_at'))}",
        ]
    )
    await safe_edit_message(
        callback,
        text=text,
        reply_markup=build_admin_review_keyboard(t, submission_id=submission_id),
    )


async def render_all_purchases(
    callback: CallbackQuery,
    *,
    status_filter: str | None,
    i18n_service,
    users_repo,
    admin_ids,
    purchase_intents_repo,
    **_,
) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await purchase_intents_repo.expire_stale()
    rows = await purchase_intents_repo.list_for_admin(status=status_filter, limit=20)
    lines = [t("admin.all_purchases_title"), ""]
    if not rows:
        lines.append(t("admin.no_purchases"))
    else:
        for row in rows:
            lines.append(
                f"• <b>{row.get('order_code', '-')}</b> | {row.get('status', '-')}\n"
                f"  {row.get('display_name') or row.get('username') or row.get('email') or '-'} | "
                f"{row.get('plan_code', '-')} | {float(row.get('final_amount_usd', 0) or 0):,.2f} USDT"
            )
    await safe_edit_message(
        callback,
        text="\n".join(lines),
        reply_markup=build_admin_purchases_keyboard(t, status_code=status_filter),
    )


async def render_promo_menu(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await safe_edit_message(
        callback,
        text=f"{t('admin.promo_codes_title')}\n\n{t('common.placeholder')}",
        reply_markup=build_admin_promo_menu_keyboard(t),
    )


async def render_promos_list(
    callback: CallbackQuery,
    *,
    i18n_service,
    users_repo,
    admin_ids,
    promo_codes_repo,
    **_,
) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await promo_codes_repo.list_recent(limit=20)
    lines = [t("admin.promos_list_title"), ""]
    if not rows:
        lines.append(t("admin.no_promos"))
    else:
        for row in rows:
            lines.append(
                f"• <b>{row.get('code', '-')}</b> | {float(row.get('discount_percent', 0) or 0):.0f}%\n"
                f"  {row.get('specific_plan_code') or 'ALL'} | {row.get('used_redemptions', 0)}/{row.get('max_redemptions', 0)} | "
                f"{t('common.enabled') if row.get('is_active') else t('common.disabled')}"
            )
    await safe_edit_message(
        callback,
        text="\n".join(lines),
        reply_markup=build_admin_promos_list_keyboard(t, rows),
    )


async def render_promo_usage(
    callback: CallbackQuery,
    *,
    i18n_service,
    users_repo,
    admin_ids,
    promo_code_redemptions_repo,
    **_,
) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await promo_code_redemptions_repo.list_recent(limit=20)
    lines = [t("admin.promo_usage_title"), ""]
    if not rows:
        lines.append(t("admin.no_promo_usage"))
    else:
        for row in rows:
            lines.append(
                f"• <b>{row.get('promo_code', '-')}</b> | {row.get('plan_code', '-')}\n"
                f"  {row.get('display_name') or row.get('username') or row.get('email') or '-'} | "
                f"{float(row.get('discount_amount', 0) or 0):,.2f} USD | {format_dt(row.get('redeemed_at'))}"
            )
    await safe_edit_message(
        callback,
        text="\n".join(lines),
        reply_markup=build_admin_promo_menu_keyboard(t),
    )


async def _notify_user_after_review(result: dict, *, approved: bool, callback: CallbackQuery, i18n_service) -> None:
    review_row = result.get("review_row") or {}
    chat_id = review_row.get("chat_id")
    if not chat_id:
        return
    lang = i18n_service.resolve_language(
        preferred_language=review_row.get("preferred_language"),
        telegram_language_code=None,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    try:
        if approved and result.get("requires_api_fix"):
            text = t("payment.approved_requires_api_fix", plan=review_row.get("plan_name", "-"))
        elif approved:
            subscription_row = result.get("subscription_row") or {}
            text = t(
                "payment.approved_user",
                plan=review_row.get("plan_name", "-"),
                ends_at=format_dt(subscription_row.get("ends_at")),
            )
        else:
            text = t("payment.rejected_user")
        await callback.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception:
        return


@router.callback_query(F.data == "adminpay:show")
async def adminpay_show(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_payments_menu(callback, **deps)


@router.callback_query(F.data == "adminpay:pending")
async def adminpay_pending(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_pending_submissions(callback, **deps)


@router.callback_query(F.data.startswith("adminpay:view:"))
async def adminpay_view(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_submission_detail(
        callback,
        submission_id=callback.data.removeprefix("adminpay:view:"),
        **deps,
    )


@router.callback_query(F.data == "adminpay:all")
async def adminpay_all(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_all_purchases(callback, status_filter=None, **deps)


@router.callback_query(F.data.startswith("adminpay:all:"))
async def adminpay_all_filtered(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_all_purchases(
        callback,
        status_filter=callback.data.removeprefix("adminpay:all:"),
        **deps,
    )


@router.callback_query(F.data.startswith("adminpay:approve:"))
async def adminpay_approve(callback: CallbackQuery, approve_manual_payment_usecase, i18n_service, users_repo, admin_ids, **deps) -> None:
    await safe_callback_answer(callback)
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    admin_row, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    result = await approve_manual_payment_usecase.execute(
        submission_id=callback.data.removeprefix("adminpay:approve:"),
        admin_user_id=admin_row["id"],
    )
    if not result.get("ok"):
        await safe_callback_answer(callback, text=t("admin.review_failed"), show_alert=True)
        return
    await _notify_user_after_review(result, approved=True, callback=callback, i18n_service=i18n_service)
    await render_pending_submissions(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids, **deps)


@router.callback_query(F.data.startswith("adminpay:reject:"))
async def adminpay_reject(callback: CallbackQuery, reject_manual_payment_usecase, i18n_service, users_repo, admin_ids, **deps) -> None:
    await safe_callback_answer(callback)
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    admin_row, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    result = await reject_manual_payment_usecase.execute(
        submission_id=callback.data.removeprefix("adminpay:reject:"),
        admin_user_id=admin_row["id"],
    )
    if not result.get("ok"):
        await safe_callback_answer(callback, text=t("admin.review_failed"), show_alert=True)
        return
    await _notify_user_after_review(result, approved=False, callback=callback, i18n_service=i18n_service)
    await render_pending_submissions(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids, **deps)


@router.callback_query(F.data == "adminpromo:show")
async def adminpromo_show(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_promo_menu(callback, **deps)


@router.callback_query(F.data == "adminpromo:list")
async def adminpromo_list(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_promos_list(callback, **deps)


@router.callback_query(F.data == "adminpromo:usage")
async def adminpromo_usage(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_promo_usage(callback, **deps)


@router.callback_query(F.data == "adminpromo:create")
async def adminpromo_create(callback: CallbackQuery, state: FSMContext, i18n_service, users_repo, admin_ids) -> None:
    await safe_callback_answer(callback)
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await state.set_state(AdminPromoStates.awaiting_payload)
    await safe_edit_message(
        callback,
        text=t("admin.create_promo_hint"),
        reply_markup=build_admin_promo_menu_keyboard(t),
    )


@router.message(AdminPromoStates.awaiting_payload)
async def adminpromo_receive_payload(
    message: Message,
    state: FSMContext,
    promo_codes_repo,
    subscription_plans_repo,
    admin_audit_logs_repo,
    i18n_service,
    users_repo,
    admin_ids,
) -> None:
    user = message.from_user
    if user is None:
        return
    context = await _ensure_admin(message, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    admin_row, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    parts = (message.text or "").split()
    if len(parts) != 4:
        await message.answer(t("admin.create_promo_invalid_format"), parse_mode="HTML")
        return
    code = parts[0].strip().upper()
    try:
        discount_percent = float(parts[1].strip())
        max_redemptions = int(parts[3].strip())
    except ValueError:
        await message.answer(t("admin.create_promo_invalid_format"), parse_mode="HTML")
        return
    if discount_percent < 0 or discount_percent > 100 or max_redemptions < 1:
        await message.answer(t("admin.create_promo_invalid_format"), parse_mode="HTML")
        return
    target = parts[2].strip().lower()
    specific_plan_id = None
    applies_to_all = target == "all"
    if not applies_to_all:
        plan_row = await subscription_plans_repo.get_by_code(target)
        if not plan_row:
            await message.answer(t("payment.error_plan_not_found"), parse_mode="HTML")
            return
        specific_plan_id = plan_row["id"]

    active_from = datetime.now(timezone.utc)
    active_to = active_from + timedelta(days=7)
    try:
        promo_row = await promo_codes_repo.create(
            code=code,
            discount_percent=discount_percent,
            max_redemptions=max_redemptions,
            active_from=active_from,
            active_to=active_to,
            applies_to_all_paid_plans=applies_to_all,
            specific_plan_id=specific_plan_id,
            created_by_user_id=admin_row["id"],
        )
    except Exception:
        await message.answer(t("admin.create_promo_invalid_format"), parse_mode="HTML")
        return
    await admin_audit_logs_repo.create(
        actor_user_id=admin_row["id"],
        target_user_id=None,
        action="promo_code_created",
        entity_type="promo_code",
        entity_id=str(promo_row["id"]),
        details_json={"code": code, "discount_percent": discount_percent, "target": target},
    )
    await state.clear()
    await message.answer(
        t("admin.promo_created", code=code, discount=int(discount_percent)),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adminpromo:disable:"))
async def adminpromo_disable(
    callback: CallbackQuery,
    promo_codes_repo,
    admin_audit_logs_repo,
    i18n_service,
    users_repo,
    admin_ids,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    admin_row, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    promo_id = callback.data.removeprefix("adminpromo:disable:")
    promo_row = await promo_codes_repo.disable(promo_id=promo_id)
    if promo_row:
        await admin_audit_logs_repo.create(
            actor_user_id=admin_row["id"],
            target_user_id=None,
            action="promo_code_disabled",
            entity_type="promo_code",
            entity_id=str(promo_id),
            details_json={"code": promo_row.get("code")},
        )
    await safe_callback_answer(callback, text=t("admin.promo_disabled"), show_alert=False)
    await render_promos_list(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids, **deps)
