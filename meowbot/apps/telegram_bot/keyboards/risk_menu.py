from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_risk_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("risk.button.stake_mode"), callback_data="risk:stake_mode"),
                InlineKeyboardButton(text=t("risk.button.stake_value"), callback_data="risk:stake_value"),
            ],
            [
                InlineKeyboardButton(text=t("risk.button.leverage"), callback_data="risk:leverage"),
                InlineKeyboardButton(text=t("risk.button.margin_limit"), callback_data="risk:margin_limit"),
            ],
            [
                InlineKeyboardButton(text=t("risk.button.warn_threshold"), callback_data="risk:warn_threshold"),
                InlineKeyboardButton(text=t("risk.button.block_threshold"), callback_data="risk:block_threshold"),
            ],
            [
                InlineKeyboardButton(text=t("risk.button.cooldown"), callback_data="risk:cooldown"),
                InlineKeyboardButton(text=t("risk.button.risk_trades_limit"), callback_data="risk:risk_trades_limit"),
            ],
            [
                InlineKeyboardButton(text=t("risk.button.sandbox_start_balance"), callback_data="risk:sandbox_start_balance"),
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:settings"),
            ],
        ]
    )


def build_stake_mode_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("risk.mode.percent"), callback_data="risk:stake_mode:set:percent"),
                InlineKeyboardButton(text=t("risk.mode.fixed"), callback_data="risk:stake_mode:set:fixed"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="risk:show"),
            ],
        ]
    )


def build_risk_parameter_keyboard(t, *, parameter: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("risk.change"), callback_data=f"risk:edit:{parameter}"),
                InlineKeyboardButton(text=t("common.back"), callback_data="risk:show"),
            ],
        ]
    )


def build_risk_saved_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("reply.home"), callback_data="menu:home"),
            ],
        ]
    )
