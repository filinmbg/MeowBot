from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meowbot.apps.telegram_bot.keyboards.api_menu import build_api_keyboard
from meowbot.apps.telegram_bot.keyboards.language_menu import build_language_keyboard
from meowbot.apps.telegram_bot.keyboards.notifications_menu import build_notifications_keyboard
from meowbot.apps.telegram_bot.keyboards.risk_menu import build_risk_keyboard
from meowbot.apps.telegram_bot.keyboards.settings_menu import (
    build_reset_stats_confirm_keyboard,
    build_mode_keyboard,
    build_settings_menu_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    bool_label,
    enabled_label,
    format_dt,
    format_money,
    html_value,
    normalize_json_mapping,
    normalize_mapping,
    safe_callback_answer,
    safe_edit_message,
)


router = Router()


def _stake_mode_key(value: str | None) -> str:
    return "risk.mode.percent" if str(value or "percent").lower() == "percent" else "risk.mode.fixed"


def _stake_value_label(runtime_row: dict) -> str:
    value = runtime_row.get("default_stake_value", "-")
    suffix = "%" if str(runtime_row.get("default_stake_mode") or "percent") == "percent" else "USDT"
    return f"{value}{suffix}"


def _notification_enabled(prefs: dict, key: str) -> bool:
    return bool(prefs.get(key, True))


async def _get_context(user, *, i18n_service, users_repo, runtime_repo, user_api_keys_repo, notification_preferences_repo, **_):
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user.id}"))
    api_row = normalize_mapping(
        await user_api_keys_repo.get_latest_key_by_runtime_user_id(runtime_user_id=f"tg:{user.id}")
    )
    prefs = (
        normalize_mapping(await notification_preferences_repo.get_by_user_id(user_row["id"]))
        if user_row
        else {}
    )
    return user_row, runtime_row, api_row, prefs, lang


async def render_settings_menu(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, _, _, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    text = f"{t('settings.title')}\n\n{t('common.placeholder')}"
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=build_settings_menu_keyboard(t))
        return
    await safe_edit_message(target, text=text, reply_markup=build_settings_menu_keyboard(t))


async def render_mode_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, runtime_row, _, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    mode_key = "mode.live" if runtime_row.get("trading_mode") == "live" else "mode.sandbox"
    text = f"{t('settings.mode_title')}\n\n<b>{t('settings.current_mode')}:</b> {t(mode_key)}"
    await safe_edit_message(target, text=text, reply_markup=build_mode_keyboard(t))


async def _validate_live_mode_switch(
    *,
    user_row: dict,
    runtime_user_id: str,
    user_subscriptions_repo,
    user_api_keys_repo,
) -> str | None:
    if str(user_row.get("status") or "active") != "active":
        return "mode.live_blocked_user_inactive"

    subscription = normalize_mapping(
        await user_subscriptions_repo.get_active_detailed_by_user_id(user_row["id"])
    )
    if not subscription:
        return "mode.live_blocked_no_subscription"

    features = normalize_json_mapping(subscription.get("features_json"))
    if not bool(features.get("live_enabled", False)):
        return "mode.live_blocked_plan"

    api_row = normalize_mapping(
        await user_api_keys_repo.get_latest_key_by_runtime_user_id(runtime_user_id=runtime_user_id)
    )
    if not api_row:
        return "mode.live_blocked_api_missing"

    if str(api_row.get("validation_status") or "").lower() != "valid" or not bool(api_row.get("is_active")):
        return "mode.live_blocked_api_invalid"

    permissions = normalize_json_mapping(api_row.get("permissions_json"))
    if not bool(permissions.get("enableFutures", False)):
        return "mode.live_blocked_api_futures"

    return None


async def render_risk_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, runtime_row, _, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    text = "\n".join(
        [
            t("settings.risk_title"),
            "",
            f"<b>{t('risk.leverage')}:</b> x{int(runtime_row.get('default_leverage', 0) or 0)}",
            f"<b>{t('risk.stake_mode')}:</b> {t(_stake_mode_key(runtime_row.get('default_stake_mode')))}",
            f"<b>{t('risk.stake_value')}:</b> {html_value(_stake_value_label(runtime_row))}",
            f"<b>{t('risk.max_margin')}:</b> {html_value(runtime_row.get('max_margin_per_trade_value', '-'))}",
            f"<b>{t('risk.warn_threshold')}:</b> {html_value(runtime_row.get('margin_ratio_warn_pct', '-'))}",
            f"<b>{t('risk.block_threshold')}:</b> {html_value(runtime_row.get('margin_ratio_block_pct', '-'))}",
            f"<b>{t('risk.risk_trades_limit')}:</b> {html_value(runtime_row.get('max_risk_trades', 5))}",
            f"<b>{t('risk.sandbox_start_balance')}:</b> {format_money(runtime_row.get('sandbox_start_balance_usd', 1000.0))}",
            f"<b>{t('risk.cooldown_status')}:</b> {enabled_label(t, bool(runtime_row.get('loss_cooldown_enabled', False)))}",
            f"<b>{t('risk.cooldown_minutes')}:</b> {html_value(runtime_row.get('loss_cooldown_minutes', '-'))}",
        ]
    )
    await safe_edit_message(target, text=text, reply_markup=build_risk_keyboard(t))


async def render_api_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, _, api_row, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    permissions = normalize_json_mapping(api_row.get("permissions_json"))
    text = "\n".join(
        [
            t("settings.api_title"),
            "",
            f"<b>{t('api.connected')}:</b> {bool_label(t, bool(api_row))}",
            f"<b>{t('api.validation_status')}:</b> {html_value(api_row.get('validation_status', '-'))}",
            f"<b>{t('api.futures')}:</b> {bool_label(t, bool(permissions.get('enableFutures', False)))}",
            f"<b>{t('api.withdrawals')}:</b> {bool_label(t, not bool(permissions.get('enableWithdrawals', False)))}",
            f"<b>{t('api.last_validation')}:</b> {format_dt(api_row.get('last_validated_at'))}",
            "",
            t("api.command_hint"),
        ]
    )
    await safe_edit_message(target, text=text, reply_markup=build_api_keyboard(t))


async def render_notifications_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, _, _, prefs, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    text = "\n".join(
        [
            t("settings.notifications_title"),
            "",
            f"<b>{t('notify.all')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notifications_enabled'))}",
            f"<b>{t('notify.open')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notify_trade_opened'))}",
            f"<b>{t('notify.tp')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notify_tp_hit'))}",
            f"<b>{t('notify.close')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notify_trade_closed'))}",
            f"<b>{t('notify.stop')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notify_stop_loss'))}",
            f"<b>{t('notify.system')}:</b> {enabled_label(t, _notification_enabled(prefs, 'notify_system'))}",
        ]
    )
    await safe_edit_message(target, text=text, reply_markup=build_notifications_keyboard(t))


async def render_language_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    user_row, _, _, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    text = "\n".join(
        [
            t("settings.language_title"),
            "",
            f"<b>{t('language.current')}:</b> {html_value((user_row or {}).get('preferred_language', '-'))}",
        ]
    )
    await safe_edit_message(target, text=text, reply_markup=build_language_keyboard(t))


async def render_reset_stats_confirm_screen(target, **deps) -> None:
    user = target.from_user
    if user is None:
        return
    _, runtime_row, _, _, lang = await _get_context(user, **deps)
    t = lambda key, **kwargs: deps["i18n_service"].t(lang, key, **kwargs)
    start_balance = runtime_row.get("sandbox_start_balance_usd", 1000.0)
    text = "\n".join(
        [
            t("settings.reset_stats_title"),
            "",
            t("settings.reset_stats_warning", balance=format_money(start_balance)),
        ]
    )
    await safe_edit_message(target, text=text, reply_markup=build_reset_stats_confirm_keyboard(t))


@router.message(lambda m: (m.text or "").strip() in {"⚙️ Налаштування", "⚙️ Settings", "⚙️ Настройки"})
async def settings_message_handler(message: Message, **deps) -> None:
    await render_settings_menu(message, **deps)


@router.callback_query(F.data == "settings:show")
async def settings_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_settings_menu(callback, **deps)


@router.callback_query(F.data == "settings:mode")
async def settings_mode_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_mode_screen(callback, **deps)


@router.callback_query(F.data == "settings:risk")
async def settings_risk_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_risk_screen(callback, **deps)


@router.callback_query(F.data == "settings:api")
async def settings_api_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_api_screen(callback, **deps)


@router.callback_query(F.data == "settings:notifications")
async def settings_notifications_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_notifications_screen(callback, **deps)


@router.callback_query(F.data == "settings:language")
async def settings_language_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_language_screen(callback, **deps)


@router.callback_query(F.data == "settings:reset_stats")
async def settings_reset_stats_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_reset_stats_confirm_screen(callback, **deps)


@router.callback_query(F.data == "settings:reset_stats:confirm")
async def settings_reset_stats_confirm_callback(
    callback: CallbackQuery,
    i18n_service,
    users_repo,
    runtime_repo,
    trades_repo,
    trade_events_repo,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    user_row, _, _, _, lang = await _get_context(
        user,
        i18n_service=i18n_service,
        users_repo=users_repo,
        runtime_repo=runtime_repo,
        user_api_keys_repo=deps["user_api_keys_repo"],
        notification_preferences_repo=deps["notification_preferences_repo"],
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    if not user_row:
        await safe_callback_answer(callback, text=t("payment.error_user_not_found"), show_alert=True)
        return

    runtime_user_id = f"tg:{user.id}"
    deleted_trades = await trades_repo.delete_trades_by_user(user_id=runtime_user_id, mode="sandbox")
    deleted_events = await trade_events_repo.delete_events_by_user(user_id=runtime_user_id, mode="sandbox")
    text = t(
        "settings.reset_stats_done",
        trades=deleted_trades,
        events=deleted_events,
    )
    await safe_edit_message(callback, text=text, reply_markup=build_settings_menu_keyboard(t))


@router.callback_query(F.data == "notify:show")
async def notify_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_notifications_screen(callback, **deps)


@router.callback_query(F.data.in_({"mode:set:sandbox", "mode:set:live"}))
async def mode_set_callback(
    callback: CallbackQuery,
    i18n_service,
    users_repo,
    trader_settings_repo,
    user_subscriptions_repo,
    user_api_keys_repo,
    trades_repo=None,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    if not user_row:
        return
    mode = "live" if callback.data.endswith(":live") else "sandbox"

    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language"),
        telegram_language_code=getattr(user, "language_code", None),
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)

    if mode == "live":
        block_key = await _validate_live_mode_switch(
            user_row=user_row,
            runtime_user_id=f"tg:{user.id}",
            user_subscriptions_repo=user_subscriptions_repo,
            user_api_keys_repo=user_api_keys_repo,
        )
        if block_key is not None:
            await safe_callback_answer(callback, text=t(block_key), show_alert=True)
            return

    updated = await trader_settings_repo.set_trading_mode_by_user_id(user_row["id"], mode)
    if not updated:
        await safe_callback_answer(callback, text=t("mode.change_failed"), show_alert=True)
        return

    if mode == "live" and trades_repo is not None:
        await trades_repo.close_open_trades_by_user_mode(
            user_id=f"tg:{user.id}",
            mode="sandbox",
            closed_at=int(datetime.now(tz=timezone.utc).timestamp() * 1000),
            reason="SWITCH_TO_LIVE",
        )

    next_deps = dict(deps)
    next_deps.update(
        {
            "i18n_service": i18n_service,
            "users_repo": users_repo,
            "trader_settings_repo": trader_settings_repo,
            "user_subscriptions_repo": user_subscriptions_repo,
            "user_api_keys_repo": user_api_keys_repo,
            "trades_repo": trades_repo,
        }
    )
    await render_mode_screen(callback, **next_deps)


@router.callback_query(F.data.startswith("notify:toggle_"))
async def notifications_toggle_callback(
    callback: CallbackQuery,
    i18n_service,
    users_repo,
    notification_preferences_repo,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    if not user_row:
        return
    prefs = normalize_mapping(await notification_preferences_repo.get_by_user_id(user_row["id"]))
    action = callback.data.removeprefix("notify:toggle_")
    if action == "all":
        current = _notification_enabled(prefs, "notifications_enabled")
        await notification_preferences_repo.update_flags_by_user_id(
            user_row["id"],
            notifications_enabled=not current,
        )
    else:
        mapping = {
            "open": "notify_trade_opened",
            "tp": "notify_tp_hit",
            "close": "notify_trade_closed",
            "stop": "notify_stop_loss",
            "system": "notify_system",
        }
        column = mapping.get(action)
        if column:
            await notification_preferences_repo.update_flags_by_user_id(
                user_row["id"],
                **{column: not _notification_enabled(prefs, column)},
            )
    next_deps = dict(deps)
    next_deps.update(
        {
            "i18n_service": i18n_service,
            "users_repo": users_repo,
            "notification_preferences_repo": notification_preferences_repo,
        }
    )
    await render_notifications_screen(callback, **next_deps)
