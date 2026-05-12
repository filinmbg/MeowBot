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
            max_risk_trades,
            sandbox_start_balance_usd,
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
            5,
            1000.0,
            true,
            120
        )
        on conflict (user_id) do nothing
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, user_id)

    async def get_by_user_id(self, user_id) -> dict | None:
        query = """
        select
            id,
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
            max_risk_trades,
            sandbox_start_balance_usd,
            loss_cooldown_enabled,
            loss_cooldown_minutes,
            created_at,
            updated_at
        from trader_settings
        where user_id = $1
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id)
        return dict(row) if row else None

    async def set_trading_mode_by_user_id(self, user_id, mode: str) -> dict | None:
        query = """
        update trader_settings
        set
            trading_mode = $2,
            updated_at = now()
        where user_id = $1
        returning
            id,
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
            max_risk_trades,
            sandbox_start_balance_usd,
            loss_cooldown_enabled,
            loss_cooldown_minutes,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, mode)
        return dict(row) if row else None

    async def update_risk_parameter_by_user_id(self, user_id, *, parameter: str, value) -> dict | None:
        allowed_columns = {
            "stake_value": "default_stake_value",
            "leverage": "default_leverage",
            "margin_limit": "max_margin_per_trade_value",
            "warn_threshold": "margin_ratio_warn_pct",
            "block_threshold": "margin_ratio_block_pct",
            "risk_trades_limit": "max_risk_trades",
            "sandbox_start_balance": "sandbox_start_balance_usd",
        }

        if parameter == "stake_mode":
            query = """
            update trader_settings
            set
                default_stake_mode = $2,
                updated_at = now()
            where user_id = $1
            returning
                id,
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
                max_risk_trades,
                sandbox_start_balance_usd,
                loss_cooldown_enabled,
                loss_cooldown_minutes,
                created_at,
                updated_at
            """
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, user_id, str(value))
            return dict(row) if row else None

        if parameter == "cooldown":
            query = """
            update trader_settings
            set
                loss_cooldown_minutes = $2,
                loss_cooldown_enabled = ($2 > 0),
                updated_at = now()
            where user_id = $1
            returning
                id,
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
                max_risk_trades,
                sandbox_start_balance_usd,
                loss_cooldown_enabled,
                loss_cooldown_minutes,
                created_at,
                updated_at
            """
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, user_id, int(value))
            return dict(row) if row else None

        if parameter not in allowed_columns:
            raise ValueError(f"Unsupported risk parameter: {parameter}")

        column = allowed_columns[parameter]
        normalized_value = int(value) if parameter in {"leverage", "risk_trades_limit"} else value
        query = f"""
        update trader_settings
        set
            {column} = $2,
            updated_at = now()
        where user_id = $1
        returning
            id,
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
            max_risk_trades,
            sandbox_start_balance_usd,
            loss_cooldown_enabled,
            loss_cooldown_minutes,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, normalized_value)
        return dict(row) if row else None
