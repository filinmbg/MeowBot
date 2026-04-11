from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def build_main_menu_keyboard(t) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t("btn_my_bot")),
                KeyboardButton(text=t("btn_stats")),
            ],
            [
                KeyboardButton(text=t("btn_trades")),
                KeyboardButton(text=t("btn_settings")),
            ],
            [
                KeyboardButton(text=t("btn_profile")),
                KeyboardButton(text=t("btn_help")),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="...",
    )


def build_onboarding_keyboard(t) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_start"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="...",
    )


def build_back_menu_keyboard(t) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_back")), KeyboardButton(text=t("btn_menu"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="...",
    )


def build_settings_keyboard(t) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_language")), KeyboardButton(text=t("btn_mode"))],
            [KeyboardButton(text=t("btn_stake")), KeyboardButton(text=t("btn_leverage"))],
            [KeyboardButton(text=t("btn_notifications"))],
            [KeyboardButton(text=t("btn_back")), KeyboardButton(text=t("btn_menu"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="...",
    )


def build_bot_control_keyboard(t, *, enabled: bool) -> ReplyKeyboardMarkup:
    toggle_text = (
        "🔴 Вимкнути бота" if enabled else "🟢 Увімкнути бота"
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=toggle_text)],
            [KeyboardButton(text=t("btn_back")), KeyboardButton(text=t("btn_menu"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="...",
    )