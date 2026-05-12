from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def build_main_menu_keyboard(t, *, is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t("reply.home")), KeyboardButton(text=t("reply.plan"))],
        [KeyboardButton(text=t("reply.trades")), KeyboardButton(text=t("reply.settings"))],
        [KeyboardButton(text=t("reply.help"))],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=t("reply.admin"))])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t("common.placeholder"),
    )


def build_onboarding_keyboard(t) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("start.button"))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t("common.placeholder"),
    )


def build_home_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("reply.plan"), callback_data="plan:show"),
                InlineKeyboardButton(text=t("reply.trades"), callback_data="trades:show"),
            ],
            [
                InlineKeyboardButton(text=t("reply.settings"), callback_data="settings:show"),
                InlineKeyboardButton(text=f"🔑 {t('settings.api')}", callback_data="api:show"),
            ],
            [
                InlineKeyboardButton(text=t("home.start_balance_button"), callback_data="risk:sandbox_start_balance"),
            ],
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data="nav:refresh:home"),
            ],
        ]
    )
