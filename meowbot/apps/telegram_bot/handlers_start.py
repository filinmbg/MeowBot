from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_main_menu_keyboard,
    build_onboarding_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import is_admin_user


router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    register_user_usecase,
    i18n_service,
    admin_ids,
    telegram_users_repo,
) -> None:
    user = message.from_user
    chat = message.chat
    if user is None:
        await message.answer("User is undefined.")
        return

    telegram_lang = getattr(user, "language_code", None)
    normalized_lang = i18n_service.normalize_language(telegram_lang)

    row = await register_user_usecase.execute(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_id=chat.id,
        language=normalized_lang,
    )

    await telegram_users_repo.upsert_user(
        telegram_id=int(user.id),
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_id=int(chat.id),
        telegram_language_code=telegram_lang,
        preferred_language=(row or {}).get("preferred_language") or normalized_lang,
    )

    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else normalized_lang,
        telegram_language_code=telegram_lang,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    if not row or not bool(row.get("is_onboarded")):
        await message.answer(
            t("start.welcome_new"),
            parse_mode="HTML",
            reply_markup=build_onboarding_keyboard(t),
        )
        return

    await message.answer(
        t("start.welcome_back"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(
            t,
            is_admin=is_admin_user(row, user.id, admin_ids),
        ),
    )


@router.message(lambda m: (m.text or "").strip() in {"🚀 Почати", "🚀 Начать", "🚀 Start"})
async def onboarding_start(
    message: Message,
    users_repo,
    telegram_profiles_repo,
    telegram_users_repo,
    i18n_service,
    admin_ids,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    await telegram_profiles_repo.mark_onboarded(user.id)
    await telegram_users_repo.mark_onboarded(user.id)
    row = await users_repo.get_by_telegram_id(user.id)

    await message.answer(
        t("start.onboarding_done"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(
            t,
            is_admin=is_admin_user(row, user.id, admin_ids),
        ),
    )
