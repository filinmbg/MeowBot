from __future__ import annotations

import asyncpg


class UserDefaultsRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_defaults_for_user(self, user_id) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into trader_settings (
                        user_id,
                        enabled,
                        mode,
                        entry_mode,
                        entry_value,
                        risk_profile,
                        allow_long,
                        allow_short,
                        night_mode,
                        quiet_hours_from,
                        quiet_hours_to,
                        max_open_trades,
                        max_daily_loss,
                        max_trades_per_day
                    )
                    values (
                        $1,
                        false,
                        'sandbox',
                        'fixed',
                        10,
                        'conservative',
                        true,
                        false,
                        false,
                        null,
                        null,
                        1,
                        null,
                        null
                    )
                    on conflict (user_id) do nothing
                    """,
                    user_id,
                )

                await conn.execute(
                    """
                    insert into notification_preferences (
                        user_id,
                        telegram_enabled,
                        email_enabled,
                        trade_alerts,
                        tp_alerts,
                        sl_alerts,
                        daily_report,
                        weekly_report,
                        system_alerts,
                        marketing_alerts,
                        critical_night_override
                    )
                    values (
                        $1,
                        true,
                        false,
                        true,
                        true,
                        true,
                        true,
                        false,
                        true,
                        false,
                        true
                    )
                    on conflict (user_id) do nothing
                    """,
                    user_id,
                )

    async def get_trader_settings(self, user_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select
                    user_id,
                    enabled,
                    mode,
                    entry_mode,
                    entry_value,
                    risk_profile,
                    allow_long,
                    allow_short,
                    night_mode,
                    quiet_hours_from,
                    quiet_hours_to,
                    max_open_trades,
                    max_daily_loss,
                    max_trades_per_day,
                    current_exchange_account_id,
                    created_at,
                    updated_at
                from trader_settings
                where user_id = $1
                """,
                user_id,
            )
        return dict(row) if row else None

    async def get_notification_preferences(self, user_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select
                    user_id,
                    telegram_enabled,
                    email_enabled,
                    trade_alerts,
                    tp_alerts,
                    sl_alerts,
                    daily_report,
                    weekly_report,
                    system_alerts,
                    marketing_alerts,
                    critical_night_override,
                    created_at,
                    updated_at
                from notification_preferences
                where user_id = $1
                """,
                user_id,
            )
        return dict(row) if row else None