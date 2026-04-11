from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_back_menu_keyboard,
    build_bot_control_keyboard,
    build_main_menu_keyboard,
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


def _mode_label(lang: str, mode: str) -> str:
    if lang == "ru":
        return "Sandbox" if mode == "sandbox" else "Live"
    if lang == "en":
        return "Sandbox" if mode == "sandbox" else "Live"
    return "Sandbox" if mode == "sandbox" else "Live"


def _yes_no_label(lang: str, value: bool) -> str:
    if lang == "ru":
        return "включены" if value else "выключены"
    if lang == "en":
        return "enabled" if value else "disabled"
    return "увімкнені" if value else "вимкнені"


def _bot_status_label(lang: str, enabled: bool) -> str:
    if lang == "ru":
        return "🟢 Включен" if enabled else "🔴 Выключен"
    if lang == "en":
        return "🟢 Enabled" if enabled else "🔴 Disabled"
    return "🟢 Увімкнений" if enabled else "🔴 Вимкнений"


def _build_my_bot_text(lang: str, data: dict) -> str:
    if lang == "ru":
        return (
            "🤖 <b>Мой бот</b>\n\n"
            f"<b>Статус:</b> {_bot_status_label(lang, bool(data.get('bot_enabled', True)))}\n"
            f"<b>Режим:</b> {_mode_label(lang, str(data.get('trading_mode', 'sandbox')))}\n"
            f"<b>Язык:</b> {data.get('preferred_language', 'ru')}\n"
            f"<b>Sandbox баланс:</b> {float(data.get('sandbox_balance_usd', 0.0)):.2f} USD\n"
            f"<b>Открытых сделок:</b> {int(data.get('open_trades_count', 0))}\n"
            f"<b>Закрытых сделок:</b> {int(data.get('closed_trades_count', 0))}\n"
            f"<b>Размер позиции:</b> {float(data.get('default_stake_value', 1.0)):.2f}%\n"
            f"<b>Плечо:</b> x{int(data.get('default_leverage', 20))}\n"
            f"<b>Уведомления:</b> {_yes_no_label(lang, bool(data.get('notifications_enabled', True)))}"
        )

    if lang == "en":
        return (
            "🤖 <b>My Bot</b>\n\n"
            f"<b>Status:</b> {_bot_status_label(lang, bool(data.get('bot_enabled', True)))}\n"
            f"<b>Mode:</b> {_mode_label(lang, str(data.get('trading_mode', 'sandbox')))}\n"
            f"<b>Language:</b> {data.get('preferred_language', 'en')}\n"
            f"<b>Sandbox balance:</b> {float(data.get('sandbox_balance_usd', 0.0)):.2f} USD\n"
            f"<b>Open trades:</b> {int(data.get('open_trades_count', 0))}\n"
            f"<b>Closed trades:</b> {int(data.get('closed_trades_count', 0))}\n"
            f"<b>Position size:</b> {float(data.get('default_stake_value', 1.0)):.2f}%\n"
            f"<b>Leverage:</b> x{int(data.get('default_leverage', 20))}\n"
            f"<b>Notifications:</b> {_yes_no_label(lang, bool(data.get('notifications_enabled', True)))}"
        )

    return (
        "🤖 <b>Мій бот</b>\n\n"
        f"<b>Статус:</b> {_bot_status_label(lang, bool(data.get('bot_enabled', True)))}\n"
        f"<b>Режим:</b> {_mode_label(lang, str(data.get('trading_mode', 'sandbox')))}\n"
        f"<b>Мова:</b> {data.get('preferred_language', 'uk')}\n"
        f"<b>Sandbox баланс:</b> {float(data.get('sandbox_balance_usd', 0.0)):.2f} USD\n"
        f"<b>Відкритих трейдів:</b> {int(data.get('open_trades_count', 0))}\n"
        f"<b>Закритих трейдів:</b> {int(data.get('closed_trades_count', 0))}\n"
        f"<b>Розмір позиції:</b> {float(data.get('default_stake_value', 1.0)):.2f}%\n"
        f"<b>Плече:</b> x{int(data.get('default_leverage', 20))}\n"
        f"<b>Нотифікації:</b> {_yes_no_label(lang, bool(data.get('notifications_enabled', True)))}"
    )


@router.message(lambda m: (m.text or "").strip() in {"🤖 Мій бот", "🤖 Мой бот", "🤖 My Bot"})
async def my_bot_handler(
    message: Message,
    telegram_users_repo,
    i18n_service,
    bot_views_uc,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    row, lang, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    data = await bot_views_uc.get_my_bot_view(user.id)

    # на всяк випадок синхронізуємо bot_enabled із профілю
    if row is not None and "bot_enabled" in row:
        data["bot_enabled"] = bool(row.get("bot_enabled", True))

    text = _build_my_bot_text(lang, data)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_bot_control_keyboard(
            t,
            enabled=bool(data.get("bot_enabled", True)),
        ),
    )


@router.message(lambda m: (m.text or "").strip() in {"🟢 Увімкнути бота"})
async def enable_bot_handler(message: Message, telegram_users_repo, i18n_service, bot_views_uc) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    await telegram_users_repo.set_bot_enabled(user.id, True)

    _, lang, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    data = await bot_views_uc.get_my_bot_view(user.id)
    data["bot_enabled"] = True

    text = _build_my_bot_text(lang, data)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_bot_control_keyboard(t, enabled=True),
    )


@router.message(lambda m: (m.text or "").strip() in {"🔴 Вимкнути бота"})
async def disable_bot_handler(message: Message, telegram_users_repo, i18n_service, bot_views_uc) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    await telegram_users_repo.set_bot_enabled(user.id, False)

    _, lang, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    data = await bot_views_uc.get_my_bot_view(user.id)
    data["bot_enabled"] = False

    text = _build_my_bot_text(lang, data)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_bot_control_keyboard(t, enabled=False),
    )


@router.message(lambda m: (m.text or "").strip() in {"📈 Трейди", "📈 Сделки", "📈 Trades"})
async def trades_handler(
    message: Message,
    telegram_users_repo,
    i18n_service,
    bot_views_uc,
    bot_views_builder,
) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, lang, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)
    data = await bot_views_uc.get_trades_view(user.id)
    text = bot_views_builder.build_trades(lang, data)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )


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
        reply_markup=build_back_menu_keyboard(t),
    )

    await message.answer(
        t("btn_language"),
        reply_markup=build_back_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"👤 Профіль", "👤 Профиль", "👤 Profile"})
async def profile_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    row, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    if not row:
        await message.answer(
            f"{t('profile_title')}\n\n{t('profile_not_found')}",
            parse_mode="HTML",
            reply_markup=build_main_menu_keyboard(t),
        )
        return

    username_line = f"<b>{t('profile_username')}:</b> @{row.get('username')}\n" if row.get("username") else ""

    await message.answer(
        f"{t('profile_title')}\n\n"
        f"<b>{t('profile_tg_id')}:</b> <code>{row.get('telegram_id')}</code>\n"
        f"{username_line}"
        f"<b>{t('profile_first_name')}:</b> {row.get('first_name') or t('unknown')}\n"
        f"<b>{t('profile_last_name')}:</b> {row.get('last_name') or t('unknown')}\n"
        f"<b>{t('profile_language')}:</b> {row.get('preferred_language') or t('unknown')}\n"
        f"<b>{t('profile_onboarded')}:</b> {t('yes') if row.get('is_onboarded') else t('no')}",
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )


@router.message(lambda m: (m.text or "").strip() in {"❓ Допомога", "❓ Помощь", "❓ Help"})
async def help_handler(message: Message, telegram_users_repo, i18n_service) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    _, _, t = await _get_lang_and_t(user, telegram_users_repo, i18n_service)

    await message.answer(
        t("help_text"),
        parse_mode="HTML",
        reply_markup=build_main_menu_keyboard(t),
    )