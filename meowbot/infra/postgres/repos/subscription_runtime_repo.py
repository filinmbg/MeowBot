from __future__ import annotations

from typing import Any

import asyncpg


class SubscriptionRuntimeRepo:
    """
    Runtime-facing Postgres repo.

    Дає:
    - список enabled users для entry-cycle
    - user settings snapshot по runtime_user_id (типу tg:123456)
    - notification target / telegram mapping
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_enabled_trading_users(self) -> list[dict[str, Any]]:
        query = """
        with symbol_cfg as (
            select
                tss.user_id,
                array_agg(tss.symbol order by tss.symbol) as enabled_symbols
            from trader_symbol_settings tss
            where tss.enabled = true
            group by tss.user_id
        ),
        timeframe_cfg as (
            select
                tfs.user_id,
                array_agg(tfs.timeframe order by tfs.timeframe) as enabled_timeframes
            from trader_timeframe_settings tfs
            where tfs.enabled = true
            group by tfs.user_id
        )
        select
            ('tg:' || tp.telegram_id::text) as trading_user_id,
            u.id as user_uuid,
            u.email,
            u.display_name,
            u.preferred_language,

            tp.telegram_id,
            tp.chat_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.is_onboarded,

            ts.trading_enabled,
            ts.trading_mode,
            ts.default_stake_mode,
            ts.default_stake_value,
            ts.default_leverage,
            ts.max_open_trades_total,
            ts.max_open_trades_per_symbol,
            ts.allow_long,
            ts.allow_short,

            ts.max_margin_per_trade_mode,
            ts.max_margin_per_trade_value,
            ts.margin_ratio_warn_pct,
            ts.margin_ratio_block_pct,

            ts.loss_cooldown_enabled,
            ts.loss_cooldown_minutes,

            np.notifications_enabled,
            np.notify_trade_opened,
            np.notify_tp_hit,
            np.notify_trade_closed,
            np.notify_stop_loss,
            np.notify_system,
            np.quiet_hours_from,
            np.quiet_hours_to,

            aus.subscription_id,
            aus.plan_id,
            aus.plan_code,
            aus.plan_name,
            aus.features_json,

            coalesce(sc.enabled_symbols, array[]::text[]) as enabled_symbols,
            coalesce(tc.enabled_timeframes, array[]::text[]) as enabled_timeframes

        from users u
        join telegram_profiles tp
            on tp.user_id = u.id
        join trader_settings ts
            on ts.user_id = u.id
        left join notification_preferences np
            on np.user_id = u.id
        join v_active_user_subscriptions aus
            on aus.user_id = u.id
        left join symbol_cfg sc
            on sc.user_id = u.id
        left join timeframe_cfg tc
            on tc.user_id = u.id
        where
            u.status = 'active'
            and tp.is_onboarded = true
            and ts.trading_enabled = true
            and (
                ts.trading_mode = 'sandbox'
                or (
                    ts.trading_mode = 'live'
                    and coalesce((aus.features_json ->> 'live_enabled')::boolean, false) = true
                )
            )
        order by u.created_at asc
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [self._normalize_row(dict(row)) for row in rows]

    async def get_by_trading_user_id(self, trading_user_id: str) -> dict[str, Any] | None:
        if not trading_user_id.startswith("tg:"):
            return None

        telegram_id_raw = trading_user_id.removeprefix("tg:")
        if not telegram_id_raw.isdigit():
            return None

        telegram_id = int(telegram_id_raw)

        query = """
        with symbol_cfg as (
            select
                tss.user_id,
                array_agg(tss.symbol order by tss.symbol) as enabled_symbols
            from trader_symbol_settings tss
            where tss.enabled = true
            group by tss.user_id
        ),
        timeframe_cfg as (
            select
                tfs.user_id,
                array_agg(tfs.timeframe order by tfs.timeframe) as enabled_timeframes
            from trader_timeframe_settings tfs
            where tfs.enabled = true
            group by tfs.user_id
        )
        select
            ('tg:' || tp.telegram_id::text) as trading_user_id,
            u.id as user_uuid,
            u.email,
            u.display_name,
            u.preferred_language,

            tp.telegram_id,
            tp.chat_id,
            tp.username,
            tp.first_name,
            tp.last_name,
            tp.is_onboarded,
            tp.last_seen_at,

            ts.trading_enabled,
            ts.trading_mode,
            ts.default_stake_mode,
            ts.default_stake_value,
            ts.default_leverage,
            ts.max_open_trades_total,
            ts.max_open_trades_per_symbol,
            ts.allow_long,
            ts.allow_short,

            ts.max_margin_per_trade_mode,
            ts.max_margin_per_trade_value,
            ts.margin_ratio_warn_pct,
            ts.margin_ratio_block_pct,

            ts.loss_cooldown_enabled,
            ts.loss_cooldown_minutes,

            np.notifications_enabled,
            np.notify_trade_opened,
            np.notify_tp_hit,
            np.notify_trade_closed,
            np.notify_stop_loss,
            np.notify_system,
            np.quiet_hours_from,
            np.quiet_hours_to,

            coalesce(sc.enabled_symbols, array[]::text[]) as enabled_symbols,
            coalesce(tc.enabled_timeframes, array[]::text[]) as enabled_timeframes

        from users u
        join telegram_profiles tp
            on tp.user_id = u.id
        left join trader_settings ts
            on ts.user_id = u.id
        left join notification_preferences np
            on np.user_id = u.id
        left join symbol_cfg sc
            on sc.user_id = u.id
        left join timeframe_cfg tc
            on tc.user_id = u.id
        where tp.telegram_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, telegram_id)

        return self._normalize_row(dict(row)) if row else None

    async def resolve_notification_target_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        row = await self.get_by_trading_user_id(user_id)
        if not row:
            return None

        return {
            "telegram_id": row.get("telegram_id"),
            "chat_id": row.get("chat_id"),
            "preferred_language": row.get("preferred_language", "uk"),
            "notifications_enabled": bool(row.get("notifications_enabled", True)),
            "username": row.get("username"),
            "first_name": row.get("first_name"),
        }

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["enabled_symbols"] = [str(x).upper() for x in (row.get("enabled_symbols") or [])]
        row["enabled_timeframes"] = [str(x) for x in (row.get("enabled_timeframes") or [])]
        return row