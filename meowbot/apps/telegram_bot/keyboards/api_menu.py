from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_api_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("api.add"), callback_data="api:add"),
                InlineKeyboardButton(text=t("api.status"), callback_data="api:status"),
            ],
            [
                InlineKeyboardButton(text=t("api.replace"), callback_data="api:replace"),
                InlineKeyboardButton(text=t("api.delete"), callback_data="api:delete"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:settings"),
            ],
        ]
    )
