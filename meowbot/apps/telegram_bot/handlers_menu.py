from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from meowbot.apps.telegram_bot.keyboards.main_menu import (
    build_home_keyboard,
    build_main_menu_keyboard,
    build_onboarding_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    api_status_key,
    current_trading_mode,
    fetch_live_account_snapshot,
    format_money,
    get_user_trade_rows,
    html_value,
    is_admin_user,
    live_balance_from_snapshot,
    normalize_mapping,
    risk_status_key,
    safe_edit_message,
    status_value,
    sum_realized_pnl,
    user_display_name,
)


router = Router()


async def _resolve_menu_state(
    message: Message,
    *,
    i18n_service,
    users_repo,
    register_user_usecase=None,
    telegram_users_repo=None,
):
    user = message.from_user
    chat = message.chat
    if user is None:
        return None, "en"

    user_row = await users_repo.get_by_telegram_id(user.id)
    telegram_lang = getattr(user, "language_code", None)

    if not user_row and register_user_usecase is not None and chat is not None:
        normalized_lang = i18n_service.normalize_language(telegram_lang)
        user_row = await register_user_usecase.execute(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            chat_id=chat.id,
            language=normalized_lang,
        )
        if telegram_users_repo is not None:
            await telegram_users_repo.upsert_user(
                telegram_id=int(user.id),
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                chat_id=int(chat.id),
                telegram_language_code=telegram_lang,
                preferred_language=(user_row or {}).get("preferred_language") or normalized_lang,
            )

    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=telegram_lang,
    )
    return user_row, lang


async def render_home_screen(
    target,
    *,
    i18n_service,
    users_repo,
    user_subscriptions_repo,
    runtime_repo,
    user_api_keys_repo,
    trades_repo,
    live_account_provider=None,
    admin_ids=None,
    **_,
) -> None:
    user = target.from_user
    if user is None:
        return

    user_row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user.id}"))
    subscription = normalize_mapping(
        await user_subscriptions_repo.get_active_detailed_by_user_id(user_row["id"])
    ) if user_row else {}
    api_row = normalize_mapping(
        await user_api_keys_repo.get_latest_key_by_runtime_user_id(runtime_user_id=f"tg:{user.id}")
    )
    current_mode = current_trading_mode(runtime_row)
    trade_rows = await get_user_trade_rows(trades_repo, user_id=f"tg:{user.id}", mode=current_mode)
    open_trades = [row for row in trade_rows if status_value(row) == "OPEN"]
    realized_pnl = sum_realized_pnl(trade_rows)
    live_snapshot: dict = {}
    current_ratio = None
    if current_mode == "live":
        live_snapshot, _ = await fetch_live_account_snapshot(
            live_account_provider,
            runtime_user_id=f"tg:{user.id}",
        )
        balance_value = live_balance_from_snapshot(live_snapshot)
        balance_text = (
            format_money(balance_value, "USDT")
            if balance_value is not None
            else t("trades.live_balance_unavailable")
        )
        current_ratio = live_snapshot.get("marginRatioPct")
    else:
        sandbox_start_balance = float(runtime_row.get("sandbox_start_balance_usd") or 1000.0)
        balance_text = format_money(sandbox_start_balance + realized_pnl)
    risk_key = risk_status_key(
        block_threshold=runtime_row.get("margin_ratio_block_pct"),
        warn_threshold=runtime_row.get("margin_ratio_warn_pct"),
        current_ratio=current_ratio,
    )
    trial_value = t("common.yes") if subscription.get("is_trial") else t("common.no")
    text = "\n".join(
        [
            t("home.title"),
            "",
            t("home.greeting", name=html_value(user_display_name(user_row))),
            f"<b>{t('home.plan')}:</b> {html_value(subscription.get('plan_name', '-'))}",
            f"<b>{t('home.trial')}:</b> {trial_value}",
            f"<b>{t('home.mode')}:</b> {t('mode.live') if current_mode == 'live' else t('mode.sandbox')}",
            f"<b>{t('home.api_status')}:</b> {t(api_status_key(api_row))}",
            f"<b>{t('home.balance')}:</b> {balance_text}",
            f"<b>{t('home.active_trades')}:</b> {len(open_trades)}",
            f"<b>{t('home.risk_status')}:</b> {t(risk_key)}",
        ]
    )
    if not api_row:
        text += f"\n\n{t('home.no_api_hint')}"

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=build_home_keyboard(t))
        return

    await safe_edit_message(target, text=text, reply_markup=build_home_keyboard(t))


async def render_help_screen(target, *, i18n_service, users_repo, **_) -> None:
    user = target.from_user
    if user is None:
        return

    user_row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    text = f"{t('help.title')}\n\n{t('help.text')}"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"\U0001f511 {t('settings.api')}", callback_data="api:show"),
                InlineKeyboardButton(text=t("reply.plan"), callback_data="plan:show"),
            ],
            [
                InlineKeyboardButton(text=t("common.back"), callback_data="nav:back:home"),
            ],
        ]
    )
    await target.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(Command("menu"))
@router.message(lambda m: (m.text or "").strip().lower() in {"menu", "\u043c\u0435\u043d\u044e"})
async def restore_main_menu_handler(message: Message, **deps) -> None:
    user = message.from_user
    if user is None:
        await message.answer("User is undefined.")
        return

    user_row, lang = await _resolve_menu_state(
        message,
        i18n_service=deps["i18n_service"],
        users_repo=deps["users_repo"],
        register_user_usecase=deps.get("register_user_usecase"),
        telegram_users_repo=deps.get("telegram_users_repo"),
    )
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)

    if not user_row or not bool(user_row.get("is_onboarded")):
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
            is_admin=is_admin_user(user_row, user.id, deps.get("admin_ids") or set()),
        ),
    )


@router.message(Command("help"))
async def help_command_handler(message: Message, **deps) -> None:
    await render_help_screen(message, **deps)


@router.message(lambda m: (m.text or "").strip() in {"\U0001f3e0 \u0413\u043e\u043b\u043e\u0432\u043d\u0430", "\U0001f3e0 Home", "\U0001f3e0 \u0413\u043b\u0430\u0432\u043d\u0430\u044f"})
async def home_message_handler(message: Message, **deps) -> None:
    await render_home_screen(message, **deps)


@router.message(lambda m: (m.text or "").strip() in {"\u2139\ufe0f \u0414\u043e\u043f\u043e\u043c\u043e\u0433\u0430", "\u2139\ufe0f Help", "\u2139\ufe0f \u041f\u043e\u043c\u043e\u0449\u044c"})
async def help_message_handler(message: Message, **deps) -> None:
    await render_help_screen(message, **deps)
