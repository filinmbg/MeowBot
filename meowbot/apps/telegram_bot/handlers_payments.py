from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from meowbot.apps.telegram_bot.handlers_plan import render_plan_upgrade_screen
from meowbot.apps.telegram_bot.keyboards.payments_menu import (
    build_invalid_promo_keyboard,
    build_payment_screen_keyboard,
    build_promo_question_keyboard,
    build_purchase_summary_keyboard,
    build_under_review_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    format_dt,
    html_value,
    normalize_mapping,
    safe_callback_answer,
    safe_edit_message,
)


router = Router()


class ManualPaymentStates(StatesGroup):
    awaiting_promo_code = State()
    summary_ready = State()
    awaiting_tx_hash = State()


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


def _back_markup(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("common.back"), callback_data="payment:back")]
        ]
    )


async def _resolve_lang(user, *, i18n_service, users_repo):
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    return user_row, lang


def _payment_reason_key(reason: str) -> str:
    mapping = {
        "user_not_found": "payment.error_user_not_found",
        "user_inactive": "payment.error_user_inactive",
        "user_blocked": "payment.error_user_blocked",
        "plan_not_found": "payment.error_plan_not_found",
        "plan_inactive": "payment.error_plan_inactive",
        "plan_not_paid": "payment.error_plan_not_paid",
        "plan_live_not_available": "payment.error_plan_live_not_available",
        "api_missing": "payment.error_api_missing",
        "api_secret_missing": "payment.error_api_missing",
        "api_invalid": "payment.error_api_invalid",
        "api_futures_disabled": "payment.error_api_futures",
        "promo_empty": "payment.promo_invalid",
        "promo_not_found": "payment.promo_invalid",
        "promo_inactive": "payment.promo_inactive",
        "promo_not_started": "payment.promo_inactive",
        "promo_expired": "payment.promo_expired",
        "promo_limit_reached": "payment.promo_limit_reached",
        "promo_plan_mismatch": "payment.promo_plan_mismatch",
        "promo_already_used": "payment.promo_already_used",
        "intent_not_found": "payment.intent_not_found",
        "intent_expired": "payment.intent_expired",
        "intent_not_submittable": "payment.intent_not_submittable",
        "payment_not_found": "payment.payment_not_found",
        "tx_hash_invalid": "payment.tx_hash_invalid",
        "wallet_not_configured": "payment.wallet_not_configured",
    }
    return mapping.get(reason, "payment.error_generic")


def _build_summary_text(
    t,
    *,
    plan_row: dict,
    base_amount,
    discount_percent,
    discount_amount,
    final_amount,
) -> str:
    discount_percent_value = discount_percent if discount_percent is not None else Decimal("0")
    return "\n".join(
        [
            t("payment.summary_title"),
            "",
            f"<b>{t('payment.plan')}:</b> {html_value(plan_row.get('name', '-'))}",
            f"<b>{t('payment.plan_code')}:</b> {html_value(plan_row.get('code', '-'))}",
            f"<b>{t('payment.base_price')}:</b> {_money(base_amount)} USDT",
            f"<b>{t('payment.discount')}:</b> {float(discount_percent_value):.0f}% ({_money(discount_amount)} USDT)",
            f"<b>{t('payment.final_price')}:</b> {_money(final_amount)} USDT",
            f"<b>{t('payment.network')}:</b> TRC20",
        ]
    )


def _build_payment_text(
    t,
    *,
    plan_name: str,
    amount,
    order_code: str,
    wallet_address: str,
    wallet_label: str | None,
    expires_at,
    support_contact: str | None,
) -> str:
    lines = [
        t("payment.screen_title"),
        "",
        f"<b>{t('payment.plan')}:</b> {html_value(plan_name)}",
        f"<b>{t('payment.amount')}:</b> {_money(amount)} USDT",
        f"<b>{t('payment.network')}:</b> TRC20",
        f"<b>{t('payment.address')}:</b> <code>{html_value(wallet_address)}</code>",
    ]
    if wallet_label:
        lines.append(f"<b>{t('payment.address_label')}:</b> {html_value(wallet_label)}")
    lines.extend(
        [
            f"<b>{t('payment.order_code')}:</b> <code>{html_value(order_code)}</code>",
            f"<b>{t('payment.expires_at')}:</b> {format_dt(expires_at)}",
            "",
            t("payment.after_payment"),
            "",
            t("payment.warning_title"),
            t("payment.warning_usdt_only"),
            t("payment.warning_trc20_only"),
            t("payment.warning_other_networks"),
        ]
    )
    if support_contact:
        lines.extend(
            [
                "",
                t("payment.support_hint", contact=support_contact),
            ]
        )
    return "\n".join(lines)


async def _render_summary(
    target,
    *,
    t,
    plan_row: dict,
    base_amount,
    discount_percent,
    discount_amount,
    final_amount,
) -> None:
    text = _build_summary_text(
        t,
        plan_row=plan_row,
        base_amount=base_amount,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        final_amount=final_amount,
    )
    markup = build_purchase_summary_keyboard(
        t,
        plan_code=plan_row["code"],
        zero_amount=Decimal(str(final_amount or 0)) <= Decimal("0"),
    )
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


@router.callback_query(F.data.startswith("payment:plan:"))
async def payment_choose_plan(
    callback: CallbackQuery,
    state: FSMContext,
    manual_payments_service,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    plan_code = callback.data.rsplit(":", 1)[-1]
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    context = await manual_payments_service.get_purchase_context_by_telegram_id(
        telegram_id=user.id,
        plan_code=plan_code,
    )
    if not context.get("ok"):
        await safe_edit_message(
            callback,
            text=t(_payment_reason_key(context.get("reason", "error_generic"))),
            reply_markup=_back_markup(t),
        )
        await state.clear()
        return

    await state.set_state(ManualPaymentStates.summary_ready)
    await state.update_data(plan_code=plan_code, promo_code=None)
    await safe_edit_message(
        callback,
        text=t("payment.promo_question"),
        reply_markup=build_promo_question_keyboard(t, plan_code=plan_code),
    )


@router.callback_query(F.data.startswith("payment:promo_yes:"))
async def payment_promo_yes(
    callback: CallbackQuery,
    state: FSMContext,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    plan_code = callback.data.rsplit(":", 1)[-1]
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await state.set_state(ManualPaymentStates.awaiting_promo_code)
    await state.update_data(plan_code=plan_code)
    await safe_edit_message(
        callback,
        text=t("payment.enter_promo"),
        reply_markup=build_invalid_promo_keyboard(t, plan_code=plan_code),
    )


@router.callback_query(F.data.startswith("payment:promo_no:"))
@router.callback_query(F.data.startswith("payment:promo_skip:"))
async def payment_promo_no(
    callback: CallbackQuery,
    state: FSMContext,
    manual_payments_service,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    plan_code = callback.data.rsplit(":", 1)[-1]
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    context = await manual_payments_service.get_purchase_context_by_telegram_id(
        telegram_id=user.id,
        plan_code=plan_code,
    )
    if not context.get("ok"):
        await safe_edit_message(
            callback,
            text=t(_payment_reason_key(context.get("reason", "error_generic"))),
            reply_markup=_back_markup(t),
        )
        await state.clear()
        return

    plan_row = context["plan_row"]
    base_amount = manual_payments_service.money(plan_row.get("price_usd", 0))
    await state.set_state(ManualPaymentStates.summary_ready)
    await state.update_data(plan_code=plan_code, promo_code=None)
    await _render_summary(
        callback,
        t=t,
        plan_row=plan_row,
        base_amount=base_amount,
        discount_percent=Decimal("0"),
        discount_amount=Decimal("0"),
        final_amount=base_amount,
    )


@router.message(ManualPaymentStates.awaiting_promo_code)
async def payment_receive_promo(
    message: Message,
    state: FSMContext,
    manual_payments_service,
    apply_promo_code_usecase,
    i18n_service,
    users_repo,
) -> None:
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    plan_code = str(data.get("plan_code") or "").strip()
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    context = await manual_payments_service.get_purchase_context_by_telegram_id(
        telegram_id=user.id,
        plan_code=plan_code,
    )
    if not context.get("ok"):
        await message.answer(t(_payment_reason_key(context.get("reason", "error_generic"))), parse_mode="HTML")
        await state.clear()
        return

    plan_row = context["plan_row"]
    result = await apply_promo_code_usecase.execute(
        user_id=context["user_row"]["id"],
        plan_row=plan_row,
        promo_code=message.text or "",
    )
    if not result.get("ok"):
        await message.answer(
            t(_payment_reason_key(result.get("reason", "promo_invalid"))),
            parse_mode="HTML",
            reply_markup=build_invalid_promo_keyboard(t, plan_code=plan_code),
        )
        return

    await state.set_state(ManualPaymentStates.summary_ready)
    await state.update_data(plan_code=plan_code, promo_code=(message.text or "").strip())
    await _render_summary(
        message,
        t=t,
        plan_row=plan_row,
        base_amount=result["base_amount"],
        discount_percent=result["discount_percent"],
        discount_amount=result["discount_amount"],
        final_amount=result["final_amount"],
    )


@router.callback_query(F.data.startswith("payment:create:"))
async def payment_create_intent(
    callback: CallbackQuery,
    state: FSMContext,
    create_manual_purchase_intent_usecase,
    submit_manual_payment_txhash_usecase,
    approve_manual_payment_usecase,
    manual_payments_service,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    plan_code = callback.data.rsplit(":", 1)[-1]
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    data = await state.get_data()
    promo_code = str(data.get("promo_code") or "").strip() or None
    result = await create_manual_purchase_intent_usecase.execute(
        telegram_id=user.id,
        plan_code=plan_code,
        promo_code=promo_code,
    )
    if not result.get("ok"):
        await safe_edit_message(
            callback,
            text=t(_payment_reason_key(result.get("reason", "error_generic"))),
            reply_markup=_back_markup(t),
        )
        await state.clear()
        return

    intent_row = result["intent_row"]
    payment_row = result.get("payment_row") or {}
    plan_row = result["plan_row"]
    reason = result.get("reason")
    if reason == "existing_under_review":
        await safe_edit_message(
            callback,
            text=t("payment.already_under_review", order_code=intent_row.get("order_code", "-")),
            reply_markup=build_under_review_keyboard(t),
        )
        await state.clear()
        return

    expected_amount = Decimal(str(payment_row.get("expected_amount", intent_row.get("final_amount_usd", 0)) or 0))
    if expected_amount <= Decimal("0"):
        submitted = await submit_manual_payment_txhash_usecase.execute(
            user_id=result["user_row"]["id"],
            purchase_intent_id=intent_row["id"],
            tx_hash=f"PROMO-FREE-{intent_row.get('order_code', '-')}",
        )
        if submitted.get("ok") and submitted.get("submission_row"):
            approved = await approve_manual_payment_usecase.execute(
                submission_id=submitted["submission_row"]["id"],
                admin_user_id=None,
                admin_comment="auto-approved-zero-amount",
            )
            if approved.get("ok"):
                text = (
                    t("payment.approved_requires_api_fix", plan=plan_row.get("name", "-"))
                    if approved.get("requires_api_fix")
                    else t(
                        "payment.approved_user",
                        plan=plan_row.get("name", "-"),
                        ends_at=format_dt((approved.get("subscription_row") or {}).get("ends_at")),
                    )
                )
                await safe_edit_message(
                    callback,
                    text=text,
                    reply_markup=build_under_review_keyboard(t),
                )
                await state.clear()
                return

    text = _build_payment_text(
        t,
        plan_name=plan_row.get("name", "-"),
        amount=expected_amount,
        order_code=intent_row.get("order_code", "-"),
        wallet_address=manual_payments_service.settings.wallet_address,
        wallet_label=manual_payments_service.settings.wallet_label,
        expires_at=intent_row.get("expires_at"),
        support_contact=manual_payments_service.settings.support_contact,
    )
    await safe_edit_message(
        callback,
        text=text,
        reply_markup=build_payment_screen_keyboard(t, intent_id=str(intent_row["id"])),
    )
    await state.clear()


@router.callback_query(F.data.startswith("payment:paid:"))
async def payment_paid_clicked(
    callback: CallbackQuery,
    state: FSMContext,
    purchase_intents_repo,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    intent_id = callback.data.removeprefix("payment:paid:")
    user_row, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    intent_row = await purchase_intents_repo.get_by_id_for_user(intent_id=intent_id, user_id=user_row.get("id"))
    if not intent_row:
        await safe_callback_answer(callback, text=t("payment.intent_not_found"), show_alert=True)
        return
    await state.set_state(ManualPaymentStates.awaiting_tx_hash)
    await state.update_data(intent_id=intent_id)
    await safe_edit_message(
        callback,
        text=t("payment.enter_tx_hash"),
        reply_markup=_back_markup(t),
    )


@router.callback_query(F.data.startswith("payment:cancel:"))
async def payment_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    purchase_intents_repo,
    payments_repo,
    i18n_service,
    users_repo,
    subscription_plans_repo,
    user_subscriptions_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    intent_id = callback.data.removeprefix("payment:cancel:")
    user_row, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    cancelled = await purchase_intents_repo.cancel_for_user(intent_id=intent_id, user_id=user_row.get("id"))
    if cancelled:
        payment_row = await payments_repo.get_by_purchase_intent_id(purchase_intent_id=intent_id)
        if payment_row:
            await payments_repo.update_status(
                payment_id=payment_row["id"],
                status="cancelled",
            )
    await state.clear()
    await render_plan_upgrade_screen(
        callback,
        i18n_service=i18n_service,
        users_repo=users_repo,
        user_subscriptions_repo=user_subscriptions_repo,
        subscription_plans_repo=subscription_plans_repo,
    )


@router.message(ManualPaymentStates.awaiting_tx_hash)
async def payment_receive_tx_hash(
    message: Message,
    state: FSMContext,
    submit_manual_payment_txhash_usecase,
    i18n_service,
    users_repo,
) -> None:
    user = message.from_user
    if user is None:
        return
    user_row, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    data = await state.get_data()
    result = await submit_manual_payment_txhash_usecase.execute(
        user_id=user_row["id"],
        purchase_intent_id=data.get("intent_id"),
        tx_hash=message.text or "",
    )
    if not result.get("ok"):
        await message.answer(t(_payment_reason_key(result.get("reason", "error_generic"))), parse_mode="HTML")
        return

    await state.clear()
    await message.answer(
        t("payment.awaiting_review"),
        parse_mode="HTML",
        reply_markup=build_under_review_keyboard(t),
    )


@router.callback_query(F.data == "payment:back")
async def payment_back_to_plans(
    callback: CallbackQuery,
    state: FSMContext,
    i18n_service,
    users_repo,
    user_subscriptions_repo,
    subscription_plans_repo,
) -> None:
    await safe_callback_answer(callback)
    await state.clear()
    await render_plan_upgrade_screen(
        callback,
        i18n_service=i18n_service,
        users_repo=users_repo,
        user_subscriptions_repo=user_subscriptions_repo,
        subscription_plans_repo=subscription_plans_repo,
    )
