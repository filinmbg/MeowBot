from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_main_menu_keyboard,
    build_onboarding_keyboard,
)


router = Router()


WELCOME_TEXT = (
    "🐾 <b>Ласкаво просимо в BotMeow</b>\n\n"
    "Це бот для контролю торгового бота, статистики, трейдів і налаштувань.\n\n"
    "Для початку натисни кнопку <b>«🚀 Старт»</b>."
)

TUTORIAL_TEXT = (
    "📘 <b>Короткий туторіал</b>\n\n"
    "Ось що ти побачиш у меню:\n\n"
    "🤖 <b>Мій бот</b> — статус бота, режим роботи, швидкий запуск\n"
    "📊 <b>Статистика</b> — результати торгівлі\n"
    "📈 <b>Трейди</b> — відкриті та останні закриті угоди\n"
    "⚙️ <b>Налаштування</b> — параметри бота\n"
    "👤 <b>Профіль</b> — інформація про акаунт\n"
    "❓ <b>Допомога</b> — підказки та пояснення\n\n"
    "Головне меню тепер буде доступне знизу постійно."
)

MAIN_MENU_TEXT = (
    "🏠 <b>Головне меню</b>\n\n"
    "Оберіть потрібний розділ кнопками знизу."
)


@router.message(CommandStart())
async def cmd_start(message: Message, telegram_users_repo) -> None:
    user = message.from_user
    chat = message.chat

    if user is None:
        await message.answer("Не вдалося визначити користувача.")
        return

    await telegram_users_repo.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        chat_id=chat.id,
    )

    is_onboarded = await telegram_users_repo.is_onboarded(user.id)

    if not is_onboarded:
        await message.answer(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=build_onboarding_keyboard(),
        )
        return

    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )


@router.message(lambda m: (m.text or "").strip() == "🚀 Старт")
async def onboarding_start(message: Message, telegram_users_repo) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Не вдалося визначити користувача.")
        return

    await telegram_users_repo.mark_onboarded(user.id)

    await message.answer(
        TUTORIAL_TEXT,
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )

    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )


@router.message(lambda m: (m.text or "").strip() in {"🏠 Меню", "Меню"})
async def show_main_menu(message: Message) -> None:
    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(),
    )