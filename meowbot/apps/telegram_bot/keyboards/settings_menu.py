from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_settings_menu_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("settings.mode"), callback_data="settings:mode"),
                InlineKeyboardButton(text=t("settings.risk"), callback_data="settings:risk"),
            ],
            [
                InlineKeyboardButton(text=t("settings.api"), callback_data="settings:api"),
                InlineKeyboardButton(text=t("settings.notifications"), callback_data="settings:notifications"),
            ],
            [
                InlineKeyboardButton(text=t("settings.language"), callback_data="settings:language"),
                InlineKeyboardButton(text=t("settings.reset_stats"), callback_data="settings:reset_stats"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )


def build_reset_stats_confirm_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("settings.reset_stats_confirm"), callback_data="settings:reset_stats:confirm"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="settings:show"),
            ],
        ]
    )


def build_mode_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("mode.sandbox"), callback_data="mode:set:sandbox"),
                InlineKeyboardButton(text=t("mode.live"), callback_data="mode:set:live"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:settings"),
            ],
        ]
    )
