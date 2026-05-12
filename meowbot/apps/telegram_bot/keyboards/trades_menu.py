from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_trades_menu_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("trades.menu.open"), callback_data="trades:open"),
                InlineKeyboardButton(text=t("trades.menu.history"), callback_data="trades:history"),
            ],
            [
                InlineKeyboardButton(text=t("trades.menu.stats"), callback_data="trades:stats"),
                InlineKeyboardButton(text=t("trades.menu.balance"), callback_data="trades:balance"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )


def build_trades_detail_keyboard(t, *, refresh_target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data=refresh_target),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:trades"),
            ],
        ]
    )


def build_trades_stats_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("trades.menu.open"), callback_data="trades:open"),
                InlineKeyboardButton(text=t("trades.menu.history"), callback_data="trades:history"),
            ],
            [
                InlineKeyboardButton(text=t("common.refresh"), callback_data="trades:stats"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )


def build_trades_history_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("trades.more"), callback_data="trades:history"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:trades"),
            ],
        ]
    )
