from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meowbot.apps.telegram_bot.keyboards.plan_menu import (
    build_plan_keyboard,
    build_plan_limits_keyboard,
    build_plan_upgrade_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    format_dt,
    format_duration_hours,
    format_money,
    html_value,
    normalize_json_mapping,
    normalize_mapping,
    safe_callback_answer,
    safe_edit_message,
)
from meowbot.core.configs.trading_plan_limits import (
    PLAN_TRADE_LIMITS,
    RISK_MAX_ACTIVE_TRADES,
    normalize_plan_code,
)


router = Router()


def _display_limit(t, value) -> str:
    if value is None:
        return t("common.unlimited")
    if value == "":
        return "-"
    return str(value)


def _is_one_trade_per_coin(value) -> bool:
    try:
        return int(value or 1) == 1
    except (TypeError, ValueError):
        return False


def _plan_limit_from_config(plan_code: str, attr: str):
    limits = PLAN_TRADE_LIMITS.get(normalize_plan_code(plan_code))
    return getattr(limits, attr) if limits is not None else None


def _plan_mode_key(features: dict) -> str:
    return "mode.live" if bool(features.get("live_enabled")) else "mode.sandbox"


def _build_upgrade_text(t, current_subscription: dict, plans: list[dict]) -> str:
    current_name = html_value(current_subscription.get("plan_name", "-"))
    current_code = html_value(current_subscription.get("plan_code", "-"))
    current_ends_at = format_dt(current_subscription.get("ends_at"))
    lines = [
        t("plan.upgrade_title"),
        "",
        f"<b>{t('plan.current_plan')}:</b> {current_name} ({current_code})",
        f"<b>{t('plan.ends_at')}:</b> {current_ends_at}",
        "",
        t("plan.available_plans"),
        "",
    ]

    if not plans:
        lines.append(t("plan.no_upgrade_options"))
    else:
        for plan in plans:
            features = normalize_json_mapping(plan.get("features_json"))
            lines.extend(
                [
                    (
                        f"• <b>{html_value(plan.get('name', '-'))}</b> "
                        f"({html_value(plan.get('code', '-'))})"
                    ),
                    f"{t('plan.price')}: {format_money(plan.get('price_usd', 0.0))}",
                    f"{t('plan.duration')}: {plan.get('duration_days', '-')}",
                    f"{t('plan.mode_access')}: {t(_plan_mode_key(features))}",
                    f"{t('plan.limit_coins')}: {features.get('coins_limit', '-')}",
                    f"{t('plan.limit_total')}: {features.get('max_open_trades_total', '-')}",
                    f"{t('plan.limit_per_coin')}: {features.get('max_open_trades_per_symbol', '-')}",
                    "",
                ]
            )

    lines.append(t("plan.upgrade_hint"))
    return "\n".join(lines)


async def render_plan_screen(target, *, i18n_service, users_repo, user_subscriptions_repo, runtime_repo, **_) -> None:
    user = target.from_user
    if user is None:
        return
    user_row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    subscription = (
        normalize_mapping(
            await user_subscriptions_repo.get_active_detailed_by_user_id(user_row["id"])
        )
        if user_row
        else {}
    )
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user.id}"))
    features = normalize_json_mapping(subscription.get("features_json"))
    ends_at = subscription.get("ends_at")
    trial_left = "-"
    if ends_at and isinstance(ends_at, datetime):
        trial_left = format_duration_hours(ends_at - datetime.now(timezone.utc))

    plan_name = subscription.get("plan_name", "-")
    plan_code = subscription.get("plan_code", "-")
    is_trial = bool(subscription.get("is_trial"))
    trading_mode = runtime_row.get("trading_mode")
    total_limit = features.get("max_open_trades_total")
    if total_limit is None:
        total_limit = runtime_row.get("max_open_trades_total", "-")
    per_coin_limit = features.get("max_open_trades_per_symbol")
    if per_coin_limit is None:
        per_coin_limit = runtime_row.get("max_open_trades_per_symbol", "-")

    text = "\n".join(
        [
            t("plan.title"),
            "",
            f"<b>{t('plan.name')}:</b> {plan_name}",
            f"<b>{t('plan.code')}:</b> {plan_code}",
            f"<b>{t('plan.trial')}:</b> {t('common.yes') if is_trial else t('common.no')}",
            f"<b>{t('plan.ends_at')}:</b> {format_dt(ends_at)}",
            f"<b>{t('plan.active_mode')}:</b> {t('mode.live') if trading_mode == 'live' else t('mode.sandbox')}",
            f"<b>{t('plan.limit_coins')}:</b> {features.get('coins_limit', '-')}",
            f"<b>{t('plan.limit_total')}:</b> {total_limit}",
            f"<b>{t('plan.limit_per_coin')}:</b> {per_coin_limit}",
            f"<b>{t('plan.trial_left')}:</b> {trial_left if is_trial else '-'}",
        ]
    )
    markup = build_plan_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


async def render_plan_limits_screen(
    target,
    *,
    i18n_service,
    users_repo,
    user_subscriptions_repo,
    runtime_repo,
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

    subscription = (
        normalize_mapping(
            await user_subscriptions_repo.get_active_detailed_by_user_id(user_row["id"])
        )
        if user_row
        else {}
    )
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user.id}"))
    features = normalize_json_mapping(subscription.get("features_json"))
    plan_code = str(subscription.get("plan_code") or "").lower()

    symbols_limit = features.get("max_symbols", features.get("coins_limit"))
    if symbols_limit is None:
        symbols_limit = _plan_limit_from_config(plan_code, "max_symbols")

    total_limit = features.get("max_open_trades_total")
    if total_limit is None and "max_open_trades_total" not in features:
        total_limit = _plan_limit_from_config(plan_code, "max_open_trades_total")
    if total_limit is None and plan_code not in {"vip"}:
        total_limit = runtime_row.get("max_open_trades_total")

    per_symbol_limit = features.get("max_open_trades_per_symbol")
    if per_symbol_limit is None:
        per_symbol_limit = runtime_row.get("max_open_trades_per_symbol", 1)

    trading_mode = runtime_row.get("trading_mode")
    risk_limit = int(runtime_row.get("max_risk_trades") or RISK_MAX_ACTIVE_TRADES)
    text = "\n".join(
        [
            t("plan.limits_title"),
            "",
            f"<b>{t('plan.name')}:</b> {html_value(subscription.get('plan_name', '-'))}",
            f"<b>{t('plan.available_symbols')}:</b> {_display_limit(t, symbols_limit)}",
            f"<b>{t('plan.max_open_trades')}:</b> {_display_limit(t, total_limit)}",
            (
                f"<b>{t('plan.one_trade_per_coin')}:</b> "
                f"{t('common.yes') if _is_one_trade_per_coin(per_symbol_limit) else _display_limit(t, per_symbol_limit)}"
            ),
            f"<b>{t('plan.global_risk_limit')}:</b> {risk_limit}",
            f"<b>{t('plan.active_mode')}:</b> {t('mode.live') if trading_mode == 'live' else t('mode.sandbox')}",
            "",
            t("plan.risk_limit_note", limit=risk_limit),
        ]
    )
    markup = build_plan_limits_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


async def render_plan_upgrade_screen(
    target,
    *,
    i18n_service,
    users_repo,
    user_subscriptions_repo,
    subscription_plans_repo,
    **_,
) -> None:
    user = target.from_user
    if user is None:
        return
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    current_subscription = (
        normalize_mapping(
            await user_subscriptions_repo.get_active_detailed_by_user_id(user_row["id"])
        )
        if user_row
        else {}
    )
    plans = await subscription_plans_repo.list_active(include_free=False, limit=10)
    text = _build_upgrade_text(t, current_subscription, plans)
    markup = build_plan_upgrade_keyboard(t, plans)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


@router.message(lambda m: (m.text or "").strip() in {"📊 Мій тариф", "📊 My Plan", "📊 Мой тариф"})
async def plan_message_handler(message: Message, **deps) -> None:
    await render_plan_screen(message, **deps)


@router.callback_query(F.data == "plan:show")
async def plan_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_screen(callback, **deps)


@router.callback_query(F.data == "plan:refresh")
async def plan_refresh_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_screen(callback, **deps)


@router.callback_query(F.data == "plan:upgrade")
async def plan_upgrade_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_upgrade_screen(callback, **deps)


@router.callback_query(F.data == "plan:limits")
async def plan_limits_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_plan_limits_screen(callback, **deps)
