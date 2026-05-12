from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from pymongo.errors import NetworkTimeout, PyMongoError

from meowbot.apps.telegram_bot.keyboards.trades_menu import (
    build_trades_detail_keyboard,
    build_trades_history_keyboard,
    build_trades_stats_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    current_trading_mode,
    fetch_live_account_snapshot,
    float_value,
    format_money,
    format_percent,
    get_user_trade_rows,
    html_value,
    live_balance_from_snapshot,
    normalize_json_mapping,
    normalize_mapping,
    realized_pnl_value,
    row_value,
    safe_callback_answer,
    safe_edit_message,
    status_value,
    sum_realized_pnl,
)


router = Router()
log = logging.getLogger("meowbot")
MONGO_UI_TIMEOUT_SECONDS = 3.0
MONGO_UI_RETRY_DELAY_SECONDS = 0.3
MONGO_UI_TRADE_ROWS_LIMIT = 100


async def _lang(user, *, i18n_service, users_repo) -> tuple[dict | None, str]:
    user_row = await users_repo.get_by_telegram_id(user.id)
    return user_row, i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )


async def render_trades_menu(target, *, i18n_service, users_repo, **_) -> None:
    await render_trades_stats(target, i18n_service=i18n_service, users_repo=users_repo, **_)


async def _sandbox_start_balance(user_id: int, runtime_repo) -> float:
    if runtime_repo is None:
        return 1000.0
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user_id}"))
    try:
        value = float(runtime_row.get("sandbox_start_balance_usd") or 1000.0)
    except (TypeError, ValueError):
        value = 1000.0
    return value if value > 0 else 1000.0


async def _runtime_mode(user_id: int, runtime_repo) -> tuple[str, dict]:
    if runtime_repo is None:
        return "sandbox", {}
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user_id}"))
    return current_trading_mode(runtime_row), runtime_row


async def _get_user_trade_rows_for_ui(
    trades_repo,
    *,
    user_id: str,
    mode: str,
    screen: str,
    limit: int = MONGO_UI_TRADE_ROWS_LIMIT,
) -> tuple[list[dict], bool]:
    started = time.perf_counter()
    attempts = 0
    last_error: Exception | None = None

    for attempt in (1, 2):
        attempts = attempt
        try:
            rows = await asyncio.wait_for(
                get_user_trade_rows(
                    trades_repo,
                    user_id=user_id,
                    mode=mode,
                    limit=limit,
                ),
                timeout=MONGO_UI_TIMEOUT_SECONDS,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.debug(
                "[telegram-trades] mongo query succeeded user_id=%s mode=%s screen=%s duration_ms=%s attempts=%s limit=%s",
                user_id,
                mode,
                screen,
                duration_ms,
                attempts,
                limit,
            )
            if attempt > 1:
                log.info(
                    "[telegram-trades] mongo query retry succeeded user_id=%s mode=%s screen=%s duration_ms=%s attempts=%s limit=%s",
                    user_id,
                    mode,
                    screen,
                    duration_ms,
                    attempts,
                    limit,
                )
            return list(rows or []), False
        except (NetworkTimeout, PyMongoError, TimeoutError, asyncio.TimeoutError) as exc:
            last_error = exc
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.warning(
                "[telegram-trades] mongo query timeout user_id=%s mode=%s screen=%s duration_ms=%s timeout_seconds=%.1f attempt=%s/%s limit=%s error=%s:%s",
                user_id,
                mode,
                screen,
                duration_ms,
                MONGO_UI_TIMEOUT_SECONDS,
                attempt,
                2,
                limit,
                type(exc).__name__,
                exc,
            )
            if attempt == 1:
                await asyncio.sleep(MONGO_UI_RETRY_DELAY_SECONDS)
                continue
            break

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.warning(
        "[telegram-trades] mongo query failed fallback user_id=%s mode=%s screen=%s duration_ms=%s timeout_seconds=%.1f attempts=%s limit=%s error=%s:%s retry_result=failed",
        user_id,
        mode,
        screen,
        duration_ms,
        MONGO_UI_TIMEOUT_SECONDS,
        attempts,
        limit,
        type(last_error).__name__ if last_error else "unknown",
        last_error or "unknown",
    )
    return [], True


async def _get_user_trade_stats_for_ui(
    trades_repo,
    *,
    user_id: str,
    mode: str,
    screen: str,
) -> tuple[dict, bool]:
    if not hasattr(trades_repo, "get_user_trade_stats"):
        rows, failed = await _get_user_trade_rows_for_ui(
            trades_repo,
            user_id=user_id,
            mode=mode,
            screen=screen,
        )
        if failed:
            return {}, True
        open_rows = [row for row in rows if status_value(row) == "OPEN"]
        closed_rows = [row for row in rows if status_value(row) == "CLOSED"]
        wins = sum(1 for row in closed_rows if realized_pnl_value(row) > 0)
        pnl = sum_realized_pnl(closed_rows)
        return {
            "open_trades": len(open_rows),
            "closed_trades": len(closed_rows),
            "wins": wins,
            "losses": max(len(closed_rows) - wins, 0),
            "realized_pnl_usd": pnl,
            "winrate": (wins / len(closed_rows) * 100) if closed_rows else 0.0,
        }, False

    started = time.perf_counter()
    attempts = 0
    last_error: Exception | None = None
    for attempt in (1, 2):
        attempts = attempt
        try:
            stats = await asyncio.wait_for(
                trades_repo.get_user_trade_stats(user_id=user_id, mode=mode),
                timeout=MONGO_UI_TIMEOUT_SECONDS,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.debug(
                "[telegram-trades] mongo stats succeeded user_id=%s mode=%s screen=%s duration_ms=%s attempts=%s",
                user_id,
                mode,
                screen,
                duration_ms,
                attempts,
            )
            return dict(stats or {}), False
        except (NetworkTimeout, PyMongoError, TimeoutError, asyncio.TimeoutError) as exc:
            last_error = exc
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.warning(
                "[telegram-trades] mongo stats timeout user_id=%s mode=%s screen=%s duration_ms=%s timeout_seconds=%.1f attempt=%s/%s error=%s:%s",
                user_id,
                mode,
                screen,
                duration_ms,
                MONGO_UI_TIMEOUT_SECONDS,
                attempt,
                2,
                type(exc).__name__,
                exc,
            )
            if attempt == 1:
                await asyncio.sleep(MONGO_UI_RETRY_DELAY_SECONDS)
                continue
            break

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.warning(
        "[telegram-trades] mongo stats failed fallback user_id=%s mode=%s screen=%s duration_ms=%s timeout_seconds=%.1f attempts=%s error=%s:%s retry_result=failed",
        user_id,
        mode,
        screen,
        duration_ms,
        MONGO_UI_TIMEOUT_SECONDS,
        attempts,
        type(last_error).__name__ if last_error else "unknown",
        last_error or "unknown",
    )
    return {}, True


async def _send_trade_data_unavailable(target, t) -> None:
    text = t("trades.data_unavailable")
    markup = build_trades_stats_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


def _format_entry_indicators(t, row: dict) -> str | None:
    entry_indicators = normalize_json_mapping(row.get("entry_indicators"))
    features = normalize_json_mapping(entry_indicators.get("features"))
    if not features:
        return None

    preferred_keys = [
        "rsi14",
        "rsi7",
        "ema20",
        "ema50",
        "atr14_pct",
        "supertrend_bullish_10_3_0",
        "volume_zscore",
        "bb_width",
    ]
    pairs: list[str] = []
    used: set[str] = set()
    for key in preferred_keys:
        if key in features:
            pairs.append(f"{key}={html_value(features.get(key))}")
            used.add(key)

    for key in sorted(features):
        if key in used:
            continue
        pairs.append(f"{key}={html_value(features.get(key))}")
        if len(pairs) >= 12:
            break

    if not pairs:
        return None
    hidden_count = max(len(features) - len(pairs), 0)
    suffix = f" (+{hidden_count})" if hidden_count else ""
    return f"  {t('trades.indicators')}: " + ", ".join(pairs) + suffix


async def render_open_trades(target, *, i18n_service, users_repo, trades_repo, runtime_repo=None, **_) -> None:
    user = target.from_user
    if user is None:
        return
    _, lang = await _lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    mode, _ = await _runtime_mode(user.id, runtime_repo)
    all_rows, db_failed = await _get_user_trade_rows_for_ui(
        trades_repo,
        user_id=f"tg:{user.id}",
        mode=mode,
        screen="open",
    )
    if db_failed:
        await _send_trade_data_unavailable(target, t)
        return
    rows = [row for row in all_rows if status_value(row) == "OPEN"]
    lines = [t("trades.open_title"), ""]
    if not rows:
        lines.append(t("trades.none_open"))
    else:
        for row in rows[:10]:
            lines.append(
                f"• <b>{row.get('symbol', '-')}</b> | {row.get('side', '-')} | {row.get('mode', '-')}\n"
                f"  {t('trades.entry')}: {row.get('entry_price', '-')} | {t('trades.pnl')}: {format_money(realized_pnl_value(row))}\n"
                f"  {t('trades.tp_sl')}: TP {row.get('tp_hit_count', 0)} / SL {row.get('sl_price', '-')}"
            )
    await safe_edit_message(
        target,
        text="\n".join(lines),
        reply_markup=build_trades_detail_keyboard(t, refresh_target="trades:open"),
    )


async def render_trades_history(target, *, i18n_service, users_repo, trades_repo, runtime_repo=None, **_) -> None:
    user = target.from_user
    if user is None:
        return
    _, lang = await _lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    mode, _ = await _runtime_mode(user.id, runtime_repo)
    all_rows, db_failed = await _get_user_trade_rows_for_ui(
        trades_repo,
        user_id=f"tg:{user.id}",
        mode=mode,
        screen="history",
    )
    if db_failed:
        await _send_trade_data_unavailable(target, t)
        return
    rows = [row for row in all_rows if status_value(row) == "CLOSED"][:10]
    lines = [t("trades.history_title"), ""]
    if not rows:
        lines.append(t("trades.none_history"))
    else:
        for row in rows:
            item_lines = [
                f"• <b>{row.get('symbol', '-')}</b> | {format_money(realized_pnl_value(row))}",
                f"  {t('trades.result')}: {row.get('close_reason', row.get('exit_reason', '-'))} | {t('trades.closed_at')}: {row.get('closed_at', row.get('opened_at', '-'))}",
            ]
            indicator_line = _format_entry_indicators(t, row)
            if indicator_line:
                item_lines.append(indicator_line)
            lines.append("\n".join(item_lines))
    await safe_edit_message(target, text="\n".join(lines), reply_markup=build_trades_history_keyboard(t))


async def render_trades_stats(
    target,
    *,
    i18n_service,
    users_repo,
    trades_repo,
    runtime_repo=None,
    live_account_provider=None,
    **_,
) -> None:
    user = target.from_user
    if user is None:
        return
    _, lang = await _lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    mode, _ = await _runtime_mode(user.id, runtime_repo)
    stats, db_failed = await _get_user_trade_stats_for_ui(
        trades_repo,
        user_id=f"tg:{user.id}",
        mode=mode,
        screen="stats",
    )
    if db_failed:
        await _send_trade_data_unavailable(target, t)
        return
    open_count = int(stats.get("open_trades", 0) or 0)
    closed_count = int(stats.get("closed_trades", 0) or 0)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", max(closed_count - wins, 0)) or 0)
    pnl = float_value(stats.get("realized_pnl_usd", 0.0))
    winrate = float_value(stats.get("winrate", (wins / closed_count * 100) if closed_count else 0.0))
    if mode == "live":
        live_snapshot, _ = await fetch_live_account_snapshot(
            live_account_provider,
            runtime_user_id=f"tg:{user.id}",
        )
        balance = live_balance_from_snapshot(live_snapshot)
        roi = (pnl / balance) * 100 if balance else 0.0
        balance_text = format_money(balance, "USDT") if balance is not None else t("trades.live_balance_unavailable")
    else:
        start_balance = await _sandbox_start_balance(user.id, runtime_repo)
        roi = (pnl / start_balance) * 100 if start_balance else 0.0
        balance_text = format_money(start_balance + pnl)
    text = "\n".join(
        [
            t("trades.stats_title"),
            "",
            f"<b>{t('home.mode')}:</b> {t('mode.live') if mode == 'live' else t('mode.sandbox')}",
            f"<b>{t('trades.open_count')}:</b> {open_count}",
            f"<b>{t('trades.closed_count')}:</b> {closed_count}",
            f"<b>{t('trades.wins')}:</b> {wins}",
            f"<b>{t('trades.losses')}:</b> {losses}",
            f"<b>{t('trades.winrate')}:</b> {format_percent(winrate)}",
            f"<b>{t('trades.realized_pnl')}:</b> {format_money(pnl)}",
            f"<b>{t('trades.roi')}:</b> {format_percent(roi)}",
            f"<b>{t('trades.balance')}:</b> {balance_text}",
        ]
    )
    markup = build_trades_stats_keyboard(t)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    await safe_edit_message(target, text=text, reply_markup=markup)


async def render_trades_balance(
    target,
    *,
    i18n_service,
    users_repo,
    trades_repo,
    user_api_keys_repo,
    runtime_repo=None,
    live_account_provider=None,
    **_,
) -> None:
    user = target.from_user
    if user is None:
        return
    _, lang = await _lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    mode, _ = await _runtime_mode(user.id, runtime_repo)
    stats, db_failed = await _get_user_trade_stats_for_ui(
        trades_repo,
        user_id=f"tg:{user.id}",
        mode=mode,
        screen="balance",
    )
    if db_failed:
        await _send_trade_data_unavailable(target, t)
        return
    pnl = float_value(stats.get("realized_pnl_usd", 0.0))
    if mode == "live":
        live_snapshot, _ = await fetch_live_account_snapshot(
            live_account_provider,
            runtime_user_id=f"tg:{user.id}",
        )
        balance = live_balance_from_snapshot(live_snapshot)
        available = live_snapshot.get("availableBalance")
        unrealized = live_snapshot.get("totalUnrealizedProfit")
        balance_text = format_money(balance, "USDT") if balance is not None else t("trades.live_balance_unavailable")
        roi = (pnl / balance) * 100 if balance else 0.0
        text = "\n".join(
            [
                t("trades.balance_title"),
                "",
                f"<b>{t('home.mode')}:</b> {t('mode.live')}",
                f"<b>{t('trades.exchange_balance')}:</b> {balance_text}",
                f"<b>{t('trades.available_balance')}:</b> {format_money(available, 'USDT') if available is not None else '-'}",
                f"<b>{t('trades.unrealized_pnl')}:</b> {format_money(unrealized, 'USDT') if unrealized is not None else '-'}",
                f"<b>{t('trades.realized_pnl')}:</b> {format_money(pnl)}",
                f"<b>{t('trades.roi')}:</b> {format_percent(roi)}",
            ]
        )
    else:
        start_balance = await _sandbox_start_balance(user.id, runtime_repo)
        balance = start_balance + pnl
        api_row = await user_api_keys_repo.get_latest_key_by_runtime_user_id(runtime_user_id=f"tg:{user.id}")
        text = "\n".join(
            [
                t("trades.balance_title"),
                "",
                f"<b>{t('home.mode')}:</b> {t('mode.sandbox')}",
                f"<b>{t('trades.sandbox_balance')}:</b> {format_money(balance)}",
                f"<b>{t('trades.sandbox_roi')}:</b> {format_percent((pnl / start_balance) * 100 if start_balance else 0.0)}",
                f"<b>{t('trades.live_summary')}:</b> {t('common.yes') if api_row else '-'}",
                f"<b>{t('trades.start_balance')}:</b> {format_money(start_balance)}",
            ]
        )
    await safe_edit_message(
        target,
        text=text,
        reply_markup=build_trades_detail_keyboard(t, refresh_target="trades:balance"),
    )


@router.message(
    lambda m: (m.text or "").strip()
    in {
        "📈 Мої трейди",
        "📈 My Trades",
        "📈 Мои сделки",
        "📊 Статистика",
        "📊 Statistics",
    }
)
async def trades_message_handler(message: Message, **deps) -> None:
    await render_trades_stats(message, **deps)


@router.callback_query(F.data == "trades:show")
async def trades_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_menu(callback, **deps)


@router.callback_query(F.data == "trades:open")
async def trades_open_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_open_trades(callback, **deps)


@router.callback_query(F.data == "trades:history")
async def trades_history_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_history(callback, **deps)


@router.callback_query(F.data == "trades:stats")
async def trades_stats_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_stats(callback, **deps)


@router.callback_query(F.data == "trades:balance")
async def trades_balance_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_balance(callback, **deps)


@router.callback_query(F.data == "trades:refresh")
async def trades_refresh_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_trades_stats(callback, **deps)
