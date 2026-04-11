from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_back_menu_keyboard,
    build_main_menu_keyboard,
    build_settings_keyboard,
)


router = Router()


async def _get_lang_and_t(user, telegram_users_repo, i18n_service):
    row = await telegram_users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    return row, lang, t


@router.message(lambda m: (m.text or "").strip() in {"⚙️ Налаштування", "⚙️ Настройки", "⚙️ Settings"})
async def settings_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_text"),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🌐 Мова", "🌐 Язык", "🌐 Language"})
async def language_menu_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_language_text"),
        parse_mode="HTML",
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        "🇺🇦 Українська\n🇷🇺 Русский\n🇬🇧 English",
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🇺🇦 Українська", "🇷🇺 Русский", "🇬🇧 English"})
async def set_language_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    text = (message.text or "").strip()

    if text == "🇺🇦 Українська":
        lang = "uk"
    elif text == "🇷🇺 Русский":
        lang = "ru"
    else:
        lang = "en"

    await telegram_users_repo.set_preferred_language(user.id, lang)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    await message.answer(
        t(f"language_changed_{lang}"),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🔄 Режим", "🔄 Режим", "🔄 Mode"})
async def mode_menu_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_mode_text"),
        parse_mode="HTML",
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        "Sandbox\nLive",
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"Sandbox", "Live"})
async def set_mode_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    raw = (message.text or "").strip()
    mode = "sandbox" if raw.lower() == "sandbox" else "live"

    row, lang, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    await telegram_users_repo.set_trading_mode(user.id, mode)

    await message.answer(
        t("mode_changed_sandbox") if mode == "sandbox" else t("mode_changed_live"),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"💰 Розмір позиції", "💰 Размер позиции", "💰 Position size"})
async def stake_menu_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_stake_text"),
        parse_mode="HTML",
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        "0.5%\n1%\n2%\n5%",
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"0.5%", "1%", "2%", "5%"})
async def set_stake_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    value_raw = (message.text or "").strip().replace("%", "")
    value = float(value_raw)

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    await telegram_users_repo.set_default_stake_value(user.id, value)

    await message.answer(
        t("stake_changed", value=value_raw),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"⚡ Плече", "⚡ Плечо", "⚡ Leverage"})
async def leverage_menu_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_leverage_text"),
        parse_mode="HTML",
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        "x10\nx20\nx50\nx100",
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"x10", "x20", "x50", "x100"})
async def set_leverage_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    value_raw = (message.text or "").strip().lower().replace("x", "")
    value = int(value_raw)

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    await telegram_users_repo.set_default_leverage(user.id, value)

    await message.answer(
        t("leverage_changed", value=value),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"🔔 Нотифікації", "🔔 Уведомления", "🔔 Notifications"})
async def notifications_menu_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("settings_notifications_text"),
        parse_mode="HTML",
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        f"{t('notifications_on')}\n{t('notifications_off')}",
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"Увімкнено", "Вимкнено", "Включены", "Выключены", "Enabled", "Disabled"})
async def set_notifications_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    text = (message.text or "").strip()
    enabled_values = {"Увімкнено", "Включены", "Enabled"}
    enabled = text in enabled_values

    await telegram_users_repo.set_notifications_enabled(user.id, enabled)

    await message.answer(
        t("notifications_enabled_text") if enabled else t("notifications_disabled_text"),
        parse_mode="HTML",
        reply_markup=build_settings_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"⬅️ Назад", "⬅️ Back"})
async def back_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("main_menu_title"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )