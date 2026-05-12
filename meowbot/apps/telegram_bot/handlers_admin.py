from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meowbot.apps.telegram_bot.keyboards.admin_menu import build_admin_detail_keyboard, build_admin_keyboard
from meowbot.apps.telegram_bot.menu_support import (
    float_value,
    format_dt,
    format_money,
    format_percent,
    get_user_trade_rows,
    is_admin_user,
    realized_pnl_value,
    row_value,
    safe_callback_answer,
    safe_edit_message,
    status_value,
    sum_realized_pnl,
)
from meowbot.core.configs.strategy_version_test_users import (
    expand_strategy_version_test_user_emails,
    normalize_strategy_plan_code,
)
from meowbot.core.services.notifications.admin_test_users_summary_message_builder import (
    AdminTestUsersSummaryMessageBuilder,
)


router = Router()


async def _admin_lang(user, *, i18n_service, users_repo):
    row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    return row, lang


async def _ensure_admin(target, *, i18n_service, users_repo, admin_ids):
    user = target.from_user
    if user is None:
        return None
    row, lang = await _admin_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    if not is_admin_user(row, user.id, admin_ids):
        if isinstance(target, CallbackQuery):
            await safe_callback_answer(target, text=i18n_service.t(lang, "common.access_denied"), show_alert=True)
        else:
            await target.answer(i18n_service.t(lang, "common.access_denied"))
        return None
    return row, lang


async def render_admin_menu(target, *, i18n_service, users_repo, admin_ids, **_) -> None:
    context = await _ensure_admin(target, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    text = f"{t('admin.title')}\n\n{t('common.placeholder')}"
    markup = build_admin_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


async def _render_admin_summary_legacy_unused(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, admin_test_users_repo, trades_repo, trade_events_repo, test_user_emails, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    users = await admin_test_users_repo.get_test_users_by_emails(
        expand_strategy_version_test_user_emails(test_user_emails)
    )
    if not users:
        await safe_edit_message(callback, text=f"{t('admin.summary_title')}\n\n{t('admin.no_data')}", reply_markup=build_admin_detail_keyboard(t, refresh_target="nav:refresh:admin_summary"))
        return
    runtime_user_ids = [row["runtime_user_id"] for row in users]
    risk_counts = await trade_events_repo.count_risk_events_by_user_ids(user_ids=runtime_user_ids, limit_hours=24)
    lines = [t("admin.summary_title"), ""]
    version_groups: dict[tuple[str, str], dict[str, float | int]] = {}
    global_groups: dict[str, dict[str, float | int]] = {}
    for row in users:
        trade_rows = await get_user_trade_rows(trades_repo, user_id=row["runtime_user_id"], mode="sandbox")
        open_rows = [item for item in trade_rows if status_value(item) == "OPEN"]
        closed_rows = [item for item in trade_rows if status_value(item) == "CLOSED"]
        pnl = sum_realized_pnl(trade_rows)
        wins = sum(1 for item in closed_rows if realized_pnl_value(item) > 0)
        roi = (pnl / 1000.0) * 100
        plan_code = normalize_strategy_plan_code(row.get("plan_code"))
        strategy_version = str(row.get("strategy_version") or "v1").lower()
        _accumulate_admin_stats(
            version_groups.setdefault(
                (plan_code, strategy_version),
                {"open": 0, "closed": 0, "wins": 0, "pnl": 0.0},
            ),
            open_count=len(open_rows),
            closed_count=len(closed_rows),
            wins=wins,
            pnl=pnl,
        )
        _accumulate_admin_stats(
            global_groups.setdefault(strategy_version, {"open": 0, "closed": 0, "wins": 0, "pnl": 0.0}),
            open_count=len(open_rows),
            closed_count=len(closed_rows),
            wins=wins,
            pnl=pnl,
        )
        lines.append(
            f"• <b>{row.get('email', '-')}</b>\n"
            f"  Plan: {plan_code.upper()} | Strategy: {strategy_version.upper()}\n"
            f"  PnL: {format_money(pnl)} | ROI: {format_percent(roi)}\n"
            f"  Open / Closed: {len(open_rows)} / {len(closed_rows)} | Wins: {wins} | Risk blocks: {risk_counts.get(row['runtime_user_id'], 0)}"
        )
    lines.append("")
    lines.extend(_build_strategy_comparison_lines(version_groups=version_groups, global_groups=global_groups))
    await safe_edit_message(callback, text="\n".join(lines), reply_markup=build_admin_detail_keyboard(t, refresh_target="nav:refresh:admin_summary"))


def _accumulate_admin_stats(
    target: dict[str, float | int],
    *,
    open_count: int,
    closed_count: int,
    wins: int,
    pnl: float,
) -> None:
    target["open"] = int(target.get("open", 0) or 0) + int(open_count)
    target["closed"] = int(target.get("closed", 0) or 0) + int(closed_count)
    target["wins"] = int(target.get("wins", 0) or 0) + int(wins)
    target["pnl"] = float(target.get("pnl", 0.0) or 0.0) + float(pnl)


def _build_strategy_comparison_lines(
    *,
    version_groups: dict[tuple[str, str], dict[str, float | int]],
    global_groups: dict[str, dict[str, float | int]],
) -> list[str]:
    lines = ["📊 <b>Strategy comparison (V1 vs V2)</b>"]
    plan_order = {"free": 0, "basic": 1, "pro": 2, "vip": 3}
    for (plan_code, strategy_version), stats in sorted(
        version_groups.items(),
        key=lambda item: (plan_order.get(item[0][0], 99), item[0][0], item[0][1]),
    ):
        closed = int(stats.get("closed", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        open_count = int(stats.get("open", 0) or 0)
        pnl = float(stats.get("pnl", 0.0) or 0.0)
        winrate = (wins / closed * 100.0) if closed else 0.0
        lines.append(
            f"{plan_code.upper()} {strategy_version.upper()}: trades={open_count + closed} "
            f"winrate={format_percent(winrate)} pnl={format_money(pnl)}"
        )

    lines.append("")
    lines.append("<b>Global V1/V2</b>")
    for strategy_version in ["v1", "v2"]:
        stats = global_groups.get(strategy_version, {"open": 0, "closed": 0, "wins": 0, "pnl": 0.0})
        closed = int(stats.get("closed", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        open_count = int(stats.get("open", 0) or 0)
        pnl = float(stats.get("pnl", 0.0) or 0.0)
        winrate = (wins / closed * 100.0) if closed else 0.0
        lines.append(
            f"{strategy_version.upper()}: trades={open_count + closed} "
            f"winrate={format_percent(winrate)} pnl={format_money(pnl)}"
        )
    return lines


async def render_admin_summary(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, admin_test_users_repo, trades_repo, trade_events_repo, test_user_emails, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    users = await admin_test_users_repo.get_test_users_by_emails(
        expand_strategy_version_test_user_emails(test_user_emails)
    )
    if not users:
        await safe_edit_message(callback, text=f"{t('admin.summary_title')}\n\n{t('admin.no_data')}", reply_markup=build_admin_detail_keyboard(t, refresh_target="nav:refresh:admin_summary"))
        return

    period_hours = 24
    runtime_user_ids = [row["runtime_user_id"] for row in users]
    risk_counts = await trade_events_repo.count_risk_events_by_user_ids(
        user_ids=runtime_user_ids,
        limit_hours=period_hours,
    )
    summary_rows: list[dict[str, object]] = []
    for row in users:
        stats = await _get_admin_user_trade_stats(
            trades_repo,
            user_id=str(row["runtime_user_id"]),
            mode="sandbox",
        )
        pnl = float(stats.get("realized_pnl_usd", 0.0) or 0.0)
        roi = (pnl / 1000.0) * 100
        summary_rows.append(
            {
                **row,
                "open_trades": int(stats.get("open_trades", 0) or 0),
                "closed_trades": int(stats.get("closed_trades", 0) or 0),
                "wins": int(stats.get("wins", 0) or 0),
                "losses": int(stats.get("losses", 0) or 0),
                "realized_pnl_usd": pnl,
                "start_balance_usd": 1000.0,
                "current_balance_usd": 1000.0 + pnl,
                "roi_pct": roi,
                "risk_blocks_period": int(risk_counts.get(row["runtime_user_id"], 0) or 0),
            }
        )

    text = AdminTestUsersSummaryMessageBuilder().build(
        summary_rows,
        period_hours=period_hours,
    )
    await safe_edit_message(callback, text=text, reply_markup=build_admin_detail_keyboard(t, refresh_target="nav:refresh:admin_summary"))


async def _get_admin_user_trade_stats(trades_repo, *, user_id: str, mode: str = "sandbox") -> dict[str, object]:
    if hasattr(trades_repo, "get_user_trade_stats"):
        try:
            stats = await trades_repo.get_user_trade_stats(user_id=user_id, mode=mode)
            return dict(stats or {})
        except Exception:
            pass

    try:
        trade_rows = await get_user_trade_rows(trades_repo, user_id=user_id, mode=mode)
    except Exception:
        return {
            "open_trades": 0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl_usd": 0.0,
        }
    open_rows = [item for item in trade_rows if status_value(item) == "OPEN"]
    closed_rows = [item for item in trade_rows if status_value(item) == "CLOSED"]
    wins = sum(1 for item in closed_rows if realized_pnl_value(item) > 0)
    losses = sum(1 for item in closed_rows if realized_pnl_value(item) <= 0)
    return {
        "open_trades": len(open_rows),
        "closed_trades": len(closed_rows),
        "wins": wins,
        "losses": losses,
        "realized_pnl_usd": sum_realized_pnl(closed_rows),
    }


async def render_admin_risk_blocks(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, trade_events_repo, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await trade_events_repo.get_recent_risk_events(limit=10)
    lines = [t("admin.risk_blocks_title"), ""]
    if not rows:
        lines.append(t("admin.no_data"))
    else:
        for row in rows:
            ts_value = datetime.fromtimestamp(int(row.get("ts", 0)) / 1000, tz=timezone.utc) if row.get("ts") else None
            lines.append(
                f"• <b>{row.get('symbol', '-')}</b> | {row.get('user_id', '-')}\n"
                f"  {row.get('event_type', '-')} | {format_dt(ts_value)}"
            )
    await safe_edit_message(callback, text="\n".join(lines), reply_markup=build_admin_detail_keyboard(t, refresh_target="admin:risk_blocks"))


async def render_admin_trial_monitor(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, user_subscriptions_repo, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await user_subscriptions_repo.list_active_trials(limit=20)
    lines = [t("admin.trial_monitor_title"), ""]
    if not rows:
        lines.append(t("admin.no_data"))
    else:
        now = datetime.now(timezone.utc)
        for row in rows:
            hours_left = 0
            if row.get("ends_at"):
                delta = row["ends_at"] - now
                hours_left = max(int(delta.total_seconds() // 3600), 0)
            lines.append(
                f"• <b>{row.get('email', '-')}</b>\n"
                f"  {t('trial.expires_at')}: {format_dt(row.get('ends_at'))}\n"
                f"  {t('trial.left', hours=hours_left)} | {t('trial.warning_sent') if row.get('trial_expiry_warning_sent_at') else t('trial.warning_not_sent')}"
            )
    await safe_edit_message(callback, text="\n".join(lines), reply_markup=build_admin_detail_keyboard(t, refresh_target="admin:trial_monitor"))


async def render_admin_api_invalid(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, user_api_keys_repo, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    rows = await user_api_keys_repo.list_invalid_users(limit=20)
    lines = [t("admin.api_invalid_title"), ""]
    if not rows:
        lines.append(t("admin.no_data"))
    else:
        for row in rows:
            lines.append(f"• <b>{row.get('email', row.get('username', '-'))}</b>\n  reason: {row.get('validation_status', '-')}")
    await safe_edit_message(callback, text="\n".join(lines), reply_markup=build_admin_detail_keyboard(t, refresh_target="admin:api_invalid"))


async def render_admin_force_reconcile(callback: CallbackQuery, *, i18n_service, users_repo, admin_ids, **_) -> None:
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    text = f"{t('admin.force_reconcile')}\n\n{t('admin.force_reconcile_hint')}"
    await safe_edit_message(callback, text=text, reply_markup=build_admin_detail_keyboard(t, refresh_target="admin:force_reconcile", include_run=True))


@router.message(lambda m: (m.text or "").strip() in {"🛠 Адмін", "🛠 Admin", "🛠 Админ"})
async def admin_message_handler(message: Message, **deps) -> None:
    await render_admin_menu(message, **deps)


@router.callback_query(F.data == "admin:show")
async def admin_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_menu(callback, **deps)


@router.callback_query(F.data == "admin:test_summary")
async def admin_test_summary_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_summary(callback, **deps)


@router.callback_query(F.data == "admin:risk_blocks")
async def admin_risk_blocks_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_risk_blocks(callback, **deps)


@router.callback_query(F.data == "admin:trial_monitor")
async def admin_trial_monitor_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_trial_monitor(callback, **deps)


@router.callback_query(F.data == "admin:api_invalid")
async def admin_api_invalid_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_api_invalid(callback, **deps)


@router.callback_query(F.data == "admin:force_reconcile")
async def admin_force_reconcile_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_admin_force_reconcile(callback, **deps)


@router.callback_query(F.data == "admin:force_reconcile:run")
async def admin_force_reconcile_run_callback(callback: CallbackQuery, i18n_service, users_repo, admin_ids) -> None:
    await safe_callback_answer(callback)
    context = await _ensure_admin(callback, i18n_service=i18n_service, users_repo=users_repo, admin_ids=admin_ids)
    if context is None:
        return
    _, lang = context
    await safe_callback_answer(callback, text=i18n_service.t(lang, "admin.force_reconcile_hint"), show_alert=True)
