from __future__ import annotations

import asyncpg


class TraderSettingsRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_default(self, user_id) -> None:
        query = """
        insert into trader_settings (
            user_id,
            trading_enabled,
            trading_mode,
            default_stake_mode,
            default_stake_value,
            default_leverage,
            max_open_trades_total,
            max_open_trades_per_symbol,
            allow_long,
            allow_short,
            max_margin_per_trade_mode,
            max_margin_per_trade_value,
            margin_ratio_warn_pct,
            margin_ratio_block_pct,
            loss_cooldown_enabled,
            loss_cooldown_minutes
        )
        values (
            $1,
            true,
            'sandbox',
            'percent',
            1.0,
            5,
            1,
            1,
            true,
            true,
            'percent',
            5.0,
            6.0,
            10.0,
            true,
            120
        )
        on conflict (user_id) do nothing
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, user_id)