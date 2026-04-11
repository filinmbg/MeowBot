from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from meowbot.apps.telegram_bot.keyboards_main import main_menu_keyboard
from meowbot.infra.postgres.repos.telegram_auth_repo import TelegramAuthRepo
from meowbot.infra.postgres.repos.user_defaults_repo import UserDefaultsRepo


router = Router()


def format_trader_status(trader: dict) -> str:
    return (
        "📊 <b>Статус трейдера</b>\n\n"
        f"Увімкнено: <b>{'так' if trader['enabled'] else 'ні'}</b>\n"
        f"Режим: <b>{trader['mode']}</b>\n"
        f"Тип входу: <b>{trader['entry_mode']}</b>\n"
        f"Значення входу: <b>{trader['entry_value']}</b>\n"
        f"Ризик-профіль: <b>{trader['risk_profile']}</b>\n"
        f"LONG: <b>{'так' if trader['allow_long'] else 'ні'}</b>\n"
        f"SHORT: <b>{'так' if trader['allow_short'] else 'ні'}</b>\n"
        f"Макс. відкритих угод: <b>{trader['max_open_trades']}</b>"
    )


@router.callback_query(F.data == "trader:status")
async def trader_status(callback: CallbackQuery, pg_pool) -> None:
    tg_user = callback.from_user

    telegram_repo = TelegramAuthRepo(pg_pool)
    defaults_repo = UserDefaultsRepo(pg_pool)

    user = await telegram_repo.get_user_by_telegram_user_id(tg_user.id)
    if not user:
        await callback.answer("Користувача не знайдено", show_alert=True)
        return

    trader = await defaults_repo.get_trader_settings(user["id"])
    if not trader:
        await callback.answer("Trader settings не знайдено", show_alert=True)
        return

    text = format_trader_status(trader)

    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_keyboard(trader["enabled"]),
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data == "trader:toggle")
async def trader_toggle(callback: CallbackQuery, pg_pool) -> None:
    tg_user = callback.from_user

    telegram_repo = TelegramAuthRepo(pg_pool)
    defaults_repo = UserDefaultsRepo(pg_pool)

    user = await telegram_repo.get_user_by_telegram_user_id(tg_user.id)
    if not user:
        await callback.answer("Користувача не знайдено", show_alert=True)
        return

    trader = await defaults_repo.toggle_trader_enabled(user["id"])
    if not trader:
        await callback.answer("Trader settings не знайдено", show_alert=True)
        return

    state_text = "увімкнено 🟢" if trader["enabled"] else "вимкнено 🔴"
    text = (
        f"⚙️ Трейдера {state_text}\n\n"
        + format_trader_status(trader)
    )

    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_keyboard(trader["enabled"]),
            parse_mode="HTML",
        )

    await callback.answer("Стан трейдера оновлено")


@router.callback_query(F.data == "menu:refresh")
async def menu_refresh(callback: CallbackQuery, pg_pool) -> None:
    tg_user = callback.from_user

    telegram_repo = TelegramAuthRepo(pg_pool)
    defaults_repo = UserDefaultsRepo(pg_pool)

    user = await telegram_repo.get_user_by_telegram_user_id(tg_user.id)
    if not user:
        await callback.answer("Користувача не знайдено", show_alert=True)
        return

    trader = await defaults_repo.get_trader_settings(user["id"])
    if not trader:
        await callback.answer("Trader settings не знайдено", show_alert=True)
        return

    text = (
        "🤖 <b>MeowBot</b>\n\n"
        f"Користувач: <b>{user.get('first_name') or user.get('display_name') or 'друже'}</b>\n"
        f"Режим: <b>{trader['mode']}</b>\n"
        f"Трейдер: <b>{'увімкнено' if trader['enabled'] else 'вимкнено'}</b>\n"
        f"Вхід: <b>{trader['entry_mode']} = {trader['entry_value']}</b>\n\n"
        "Оберіть дію:"
    )

    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=main_menu_keyboard(trader["enabled"]),
            parse_mode="HTML",
        )

    await callback.answer("Меню оновлено")