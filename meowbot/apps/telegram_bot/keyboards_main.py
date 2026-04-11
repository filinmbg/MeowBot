from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Вимкнути трейдера" if enabled else "🟢 Увімкнути трейдера"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статус трейдера", callback_data="trader:status"),
            ],
            [
                InlineKeyboardButton(text=toggle_text, callback_data="trader:toggle"),
            ],
            [
                InlineKeyboardButton(text="🔄 Оновити меню", callback_data="menu:refresh"),
            ],
        ]
    )