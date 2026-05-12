from __future__ import annotations

import json
import inspect
import logging
from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


log = logging.getLogger("meowbot")


def user_display_name(user_row: dict | None) -> str:
    if not user_row:
        return "User"
    for key in ("display_name", "first_name", "username", "email"):
        value = user_row.get(key)
        if value:
            return str(value)
    return "User"


def normalize_mapping(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if hasattr(raw, "items"):
        try:
            return dict(raw)
        except Exception:
            return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_json_mapping(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return normalize_mapping(raw)


def bool_label(t, value: bool) -> str:
    return t("common.yes") if value else t("common.no")


def enabled_label(t, value: bool) -> str:
    return t("common.enabled") if value else t("common.disabled")


def format_money(value, suffix: str = "USD") -> str:
    if value is None:
        return "-"
    return f"{float(value):,.2f} {suffix}"


def format_percent(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}%"


def format_dt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def format_duration_hours(value) -> str:
    if value is None:
        return "-"
    total_seconds = max(int(value.total_seconds()), 0)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def risk_status_key(*, block_threshold, warn_threshold, current_ratio: float | None = None) -> str:
    if current_ratio is None:
        return "status.low"
    if block_threshold is not None and current_ratio >= float(block_threshold):
        return "status.blocked"
    if warn_threshold is not None and current_ratio >= float(warn_threshold):
        return "status.high"
    if current_ratio > 0:
        return "status.medium"
    return "status.low"


def api_status_key(api_row: dict | None) -> str:
    if not api_row:
        return "api.not_connected"
    status = str(api_row.get("validation_status") or "").lower()
    if status == "valid":
        return "api.connected"
    return "status.invalid"


def html_value(value) -> str:
    return escape(str(value)) if value is not None else "-"


def float_value(value, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def status_value(row: Any) -> str:
    value = row_value(row, "status", "")
    return str(value.value if hasattr(value, "value") else value)


def realized_pnl_value(row: Any) -> float:
    mode = str(row_value(row, "mode", "") or "").lower()
    exchange_pnl = row_value(row, "exchange_net_realized_pnl_usd", None)
    if exchange_pnl is None:
        exchange_pnl = row_value(row, "exchange_net_realized_pnl_usdt", None)
    if exchange_pnl is None:
        exchange_pnl = row_value(row, "exchange_realized_pnl_usd", None)
    if exchange_pnl is None:
        exchange_pnl = row_value(row, "exchange_realized_pnl_usdt", None)
    if mode == "live" and exchange_pnl is not None:
        return float_value(exchange_pnl, 0.0)
    return float_value(row_value(row, "realized_pnl_usd", 0.0))


def sum_realized_pnl(rows: list[Any]) -> float:
    return sum(realized_pnl_value(row) for row in rows)


def current_trading_mode(runtime_row: dict | None) -> str:
    return "live" if str((runtime_row or {}).get("trading_mode") or "").lower() == "live" else "sandbox"


def live_balance_from_snapshot(snapshot: dict | None) -> float | None:
    for key in ("totalWalletBalance", "totalMarginBalance", "availableBalance"):
        value = (snapshot or {}).get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


async def fetch_live_account_snapshot(
    live_account_provider,
    *,
    runtime_user_id: str,
) -> tuple[dict[str, Any], str | None]:
    if live_account_provider is None:
        return {}, "live_account_provider_missing"

    client = None
    try:
        client, _, err = await live_account_provider.build_client(
            runtime_user_id=runtime_user_id,
            require_active=True,
        )
        if err is not None or client is None:
            return {}, err or "live_account_client_unavailable"

        snapshot = await client.get_account_risk_snapshot()
        if not snapshot:
            return {}, "live_account_snapshot_unavailable"
        return normalize_mapping(snapshot), None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                await client.close()
            except Exception:
                pass


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def get_user_trade_rows(
    trades_repo,
    *,
    user_id: str,
    mode: str | None = "sandbox",
    limit: int = 100,
) -> list[Any]:
    try:
        safe_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        safe_limit = 100

    if hasattr(trades_repo, "get_trade_rows_by_user"):
        return await _maybe_await(
            trades_repo.get_trade_rows_by_user(user_id=user_id, mode=mode, limit=safe_limit)
        )
    if hasattr(trades_repo, "get_trades"):
        return await _maybe_await(
            trades_repo.get_trades(user_id=user_id, mode=mode, limit=safe_limit)
        )

    try:
        closed = await _maybe_await(trades_repo.get_closed_trades(user_id=user_id, limit=safe_limit))
    except TypeError:
        closed = await _maybe_await(trades_repo.get_closed_trades(user_id=user_id))
    open_rows = await _maybe_await(trades_repo.get_open_trades_by_user(user_id, limit=safe_limit))
    return list(closed) + list(open_rows)


def is_admin_user(user_row: dict | None, telegram_id: int, admin_ids: set[str] | None) -> bool:
    if user_row and str(user_row.get("role")) == "admin":
        return True
    return str(telegram_id) in (admin_ids or set())


async def safe_edit_message(
    callback: CallbackQuery,
    *,
    text: str,
    reply_markup,
) -> None:
    message = callback.message
    if message is None:
        return

    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


async def safe_callback_answer(
    callback: CallbackQuery,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if (
            "query is too old" in message
            or "response timeout expired" in message
            or "query id is invalid" in message
        ):
            log.debug("[telegram-menu] callback answer skipped: query too old or invalid")
            return
        raise
