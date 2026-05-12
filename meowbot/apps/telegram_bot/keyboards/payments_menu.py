from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


def build_payment_plan_select_keyboard(t, plans: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("payment.buy_plan_button", plan=plan.get("name", "-"), amount=_money(plan.get("price_usd"))),
                    callback_data=f"payment:plan:{plan.get('code')}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.refresh"), callback_data="plan:upgrade"),
            InlineKeyboardButton(text=t("common.back"), callback_data="plan:show"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_promo_question_keyboard(t, *, plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("common.yes"), callback_data=f"payment:promo_yes:{plan_code}"),
                InlineKeyboardButton(text=t("common.no"), callback_data=f"payment:promo_no:{plan_code}"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="payment:back"),
            ],
        ]
    )


def build_invalid_promo_keyboard(t, *, plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("payment.retry_promo"), callback_data=f"payment:promo_yes:{plan_code}"),
                InlineKeyboardButton(text=t("payment.skip_promo"), callback_data=f"payment:promo_no:{plan_code}"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="payment:back"),
            ],
        ]
    )


def build_purchase_summary_keyboard(t, *, plan_code: str, zero_amount: bool = False) -> InlineKeyboardMarkup:
    action_key = "payment.activate_free" if zero_amount else "payment.pay_usdt"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(action_key), callback_data=f"payment:create:{plan_code}"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="payment:back"),
            ],
        ]
    )


def build_payment_screen_keyboard(t, *, intent_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("payment.i_paid"), callback_data=f"payment:paid:{intent_id}"),
                InlineKeyboardButton(text=t("payment.cancel"), callback_data=f"payment:cancel:{intent_id}"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="payment:back"),
            ],
        ]
    )


def build_under_review_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("reply.plan"), callback_data="plan:show"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ]
        ]
    )


def build_admin_payments_menu_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.pending_submissions"), callback_data="adminpay:pending"),
                InlineKeyboardButton(text=t("admin.all_purchases"), callback_data="adminpay:all"),
            ],
            [
                InlineKeyboardButton(text=t("admin.promo_codes"), callback_data="adminpromo:show"),
                InlineKeyboardButton(text=t("admin.trial_activations"), callback_data="admin:trial_monitor"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:admin"),
            ],
        ]
    )


def build_admin_pending_list_keyboard(t, submissions: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in submissions:
        label = t("admin.pending_submission_button", order_code=row.get("order_code", "-"), plan=row.get("plan_code", "-"))
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"adminpay:view:{row.get('id')}")]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.refresh"), callback_data="adminpay:pending"),
            InlineKeyboardButton(text=t("common.back"), callback_data="adminpay:show"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_review_keyboard(t, *, submission_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.approve"), callback_data=f"adminpay:approve:{submission_id}"),
                InlineKeyboardButton(text=t("admin.reject"), callback_data=f"adminpay:reject:{submission_id}"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="adminpay:pending"),
            ],
        ]
    )


def build_admin_purchases_keyboard(t, *, status_code: str | None = None) -> InlineKeyboardMarkup:
    all_callback = "adminpay:all"
    pending_callback = "adminpay:all:awaiting_manual_check"
    approved_callback = "adminpay:all:approved"
    rejected_callback = "adminpay:all:rejected"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.filter_all"), callback_data=all_callback),
                InlineKeyboardButton(text=t("admin.filter_pending"), callback_data=pending_callback),
            ],
            [
                InlineKeyboardButton(text=t("admin.filter_approved"), callback_data=approved_callback),
                InlineKeyboardButton(text=t("admin.filter_rejected"), callback_data=rejected_callback),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="adminpay:show"),
            ],
        ]
    )


def build_admin_promo_menu_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.create_promo"), callback_data="adminpromo:create"),
                InlineKeyboardButton(text=t("admin.list_promos"), callback_data="adminpromo:list"),
            ],
            [
                InlineKeyboardButton(text=t("admin.promo_usage"), callback_data="adminpromo:usage"),
                InlineKeyboardButton(text=t("common.back"), callback_data="adminpay:show"),
            ],
        ]
    )


def build_admin_promos_list_keyboard(t, promos: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for promo in promos:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin.disable_promo_button", code=promo.get("code", "-")),
                    callback_data=f"adminpromo:disable:{promo.get('id')}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("common.refresh"), callback_data="adminpromo:list"),
            InlineKeyboardButton(text=t("common.back"), callback_data="adminpromo:show"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
