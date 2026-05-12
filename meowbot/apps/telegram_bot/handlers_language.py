from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from meowbot.apps.telegram_bot.handlers_settings import render_language_screen
from meowbot.apps.telegram_bot.menu_support import safe_callback_answer


router = Router()


@router.callback_query(F.data == "language:show")
async def language_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_language_screen(callback, **deps)


@router.callback_query(F.data.startswith("language:set:"))
async def language_set_callback(
    callback: CallbackQuery,
    i18n_service,
    users_repo,
    telegram_users_repo,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    lang = i18n_service.normalize_language(callback.data.split(":")[-1])
    await users_repo.set_preferred_language_by_telegram_id(user.id, lang)
    await telegram_users_repo.set_preferred_language(user.id, lang)
    next_deps = dict(deps)
    next_deps.update({"i18n_service": i18n_service, "users_repo": users_repo})
    await render_language_screen(callback, **next_deps)
