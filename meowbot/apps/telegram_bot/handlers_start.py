from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_main_menu_keyboard,
    build_onboarding_keyboard,
)


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    chat = message.chat

    if user is None:
        await message.answer("User is undefined.")
        return

    telegram_lang = getattr(user, "language_code", None)
    normalized_lang = i18n_service.normalize_language(telegram_lang)

    row = await telegram_users_repo.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_id=chat.id,
        telegram_language_code=telegram_lang,
        preferred_language=normalized_lang,
    )

    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else normalized_lang,
        telegram_language_code=telegram_lang,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    is_onboarded = bool(row.get("is_onboarded", False)) if row else False

    if not is_onboarded:
        await message.answer(
            t("start_welcome_new"),
            parse_mode="HTML",
            reply_markup=build_onboarding_keyboard(t),
        )
        return

    await message.answer(
        t("start_welcome_old"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🚀 Почати", "🚀 Начать", "🚀 Start"})
async def onboarding_start(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    row = await telegram_users_repo.get_by_telegram_id(user.id)
    preferred_language = row.get("preferred_language") if row else None
    telegram_language_code = getattr(user, "language_code", None)

    lang = i18n_service.resolve_language(
        preferred_language=preferred_language,
        telegram_language_code=telegram_language_code,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    await telegram_users_repo.mark_onboarded(user.id)

    await message.answer(
        t("tutorial_text"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )

    await message.answer(
        t("main_menu_title"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🏠 Меню", "🏠 Menu"})
async def show_main_menu(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    row = await telegram_users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    await message.answer(
        t("main_menu_title"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )