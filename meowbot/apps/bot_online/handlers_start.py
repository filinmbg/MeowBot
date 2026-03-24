from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from meowbot.apps.bot_online.bootstrap import build_get_or_create_user_by_telegram_usecase


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    app = message.bot.get("app_context")
    pg_pool = app["pg_pool"]

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

    if is_new_user:
        await message.answer(
            f"Привіт, {user.get('first_name') or user.get('display_name') or 'друже'}! "
            f"Твій акаунт створено ✅\n"
            f"user_id: {user['id']}"
        )
    else:
        await message.answer(
            f"З поверненням, {user.get('first_name') or user.get('display_name') or 'друже'} 👋\n"
            f"user_id: {user['id']}"
        )