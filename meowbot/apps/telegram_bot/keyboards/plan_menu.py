from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from meowbot.apps.telegram_bot.keyboards.payments_menu import build_payment_plan_select_keyboard


def build_plan_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("plan.add_api"), callback_data="api:add"),
                InlineKeyboardButton(text=t("plan.limits"), callback_data="plan:limits"),
            ],
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data="plan:refresh"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )


def build_plan_limits_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data="plan:limits"),
                InlineKeyboardButton(text=t("common.back"), callback_data="plan:show"),
            ],
        ]
    )


def build_plan_upgrade_keyboard(t, plans: list[dict] | None = None) -> InlineKeyboardMarkup:
    if plans:
        return build_payment_plan_select_keyboard(t, plans)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data="plan:upgrade"),
                InlineKeyboardButton(text=t("common.back"), callback_data="plan:show"),
            ],
        ]
    )
