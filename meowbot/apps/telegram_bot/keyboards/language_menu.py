from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_language_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("language.uk"), callback_data="language:set:uk"),
                InlineKeyboardButton(text=t("language.en"), callback_data="language:set:en"),
            ],
            [
                InlineKeyboardButton(text=t("language.ru"), callback_data="language:set:ru"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:settings"),
            ],
        ]
    )
