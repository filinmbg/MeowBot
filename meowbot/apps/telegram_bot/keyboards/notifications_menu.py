from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_notifications_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("notify.all"), callback_data="notify:toggle_all"),
                InlineKeyboardButton(text=t("notify.open"), callback_data="notify:toggle_open"),
            ],
            [
                InlineKeyboardButton(text=t("notify.tp"), callback_data="notify:toggle_tp"),
                InlineKeyboardButton(text=t("notify.close"), callback_data="notify:toggle_close"),
            ],
            [
                InlineKeyboardButton(text=t("notify.stop"), callback_data="notify:toggle_stop"),
                InlineKeyboardButton(text=t("notify.system"), callback_data="notify:toggle_system"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:settings"),
            ],
        ]
    )
