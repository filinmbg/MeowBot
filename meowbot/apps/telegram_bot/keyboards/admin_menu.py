from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_admin_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.test_summary"), callback_data="admin:test_summary"),
                InlineKeyboardButton(text=t("admin.risk_blocks"), callback_data="admin:risk_blocks"),
            ],
            [
                InlineKeyboardButton(text=t("admin.trial_monitor"), callback_data="admin:trial_monitor"),
                InlineKeyboardButton(text=t("admin.api_invalid"), callback_data="admin:api_invalid"),
            ],
            [
                InlineKeyboardButton(text=t("admin.manual_payments"), callback_data="adminpay:show"),
                InlineKeyboardButton(text=t("admin.promo_codes"), callback_data="adminpromo:show"),
            ],
            [
                InlineKeyboardButton(text=t("admin.force_reconcile"), callback_data="admin:force_reconcile"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )


def build_admin_detail_keyboard(t, *, refresh_target: str, include_run: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if include_run:
        rows.append([InlineKeyboardButton(text=t("admin.force_run"), callback_data="admin:force_reconcile:run")])
    rows.append(
        [
            InlineKeyboardButton(text=t("common.refresh"), callback_data=refresh_target),
            InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:admin"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
