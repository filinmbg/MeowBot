from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from meowbot.apps.telegram_bot.bootstrap import build_get_or_create_user_by_telegram_usecase
from meowbot.infra.postgres.repos.user_defaults_repo import UserDefaultsRepo


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, pg_pool) -> None:
    tg_user = message.from_user
    if tg_user is None:
        await message.answer("Не вдалося визначити Telegram-користувача.")
        return

    usecase = build_get_or_create_user_by_telegram_usecase(pg_pool)

    user, is_new_user = await usecase.execute(
        telegram_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        language_code=tg_user.language_code or "uk",
    )

    defaults_repo = UserDefaultsRepo(pg_pool)
    trader = await defaults_repo.get_trader_settings(user["id"])

    name = user.get("first_name") or user.get("display_name") or "друже"

    if is_new_user:
        await message.answer(
            f"Привіт, {name}! ✅\n\n"
            f"Акаунт створено.\n"
            f"Трейдер ініціалізовано.\n\n"
            f"Режим: {trader['mode']}\n"
            f"Увімкнено: {'так' if trader['enabled'] else 'ні'}\n"
            f"Вхід: {trader['entry_mode']} = {trader['entry_value']}\n"
            f"user_id: {user['id']}"
        )
    else:
        await message.answer(
            f"З поверненням, {name} 👋\n\n"
            f"Режим: {trader['mode']}\n"
            f"Увімкнено: {'так' if trader['enabled'] else 'ні'}\n"
            f"Вхід: {trader['entry_mode']} = {trader['entry_value']}\n"
            f"user_id: {user['id']}"
        )