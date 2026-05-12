from __future__ import annotations

import json
from typing import Any

import asyncpg


class UserApiKeysRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    def _jsonb_payload(self, value: dict[str, Any] | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    async def get_active_key_by_runtime_user_id(
        self,
        *,
        runtime_user_id: str,
        exchange: str = "binance",
    ) -> dict[str, Any] | None:
        if not runtime_user_id.startswith("tg:"):
            return None

        telegram_id_raw = runtime_user_id.removeprefix("tg:")
        if not telegram_id_raw.isdigit():
            return None

        telegram_id = int(telegram_id_raw)

        query = """
        select
            u.id as user_uuid,
            u.email,
            tp.telegram_id,
            k.id,
            k.user_id,
            k.exchange,
            k.label,
            k.encrypted_api_key,
            k.encrypted_api_secret,
            k.encrypted_passphrase,
            k.api_key_fingerprint,
            k.exchange_account_fingerprint,
            k.permissions_json,
            k.permissions_checked_at,
            k.validation_status,
            k.is_active,
            k.last_validated_at,
            k.created_at,
            k.updated_at
        from telegram_profiles tp
        join users u
            on u.id = tp.user_id
        join user_api_keys k
            on k.user_id = u.id
        where
            tp.telegram_id = $1
            and k.exchange = $2
            and k.is_active = true
            and k.validation_status = 'valid'
        order by
            k.last_validated_at desc nulls last,
            k.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, telegram_id, exchange)
        return dict(row) if row else None

    async def upsert_user_api_key(
        self,
        *,
        user_id,
        exchange: str,
        label: str,
        encrypted_api_key: str,
        encrypted_api_secret: str,
        encrypted_passphrase: str | None,
        api_key_fingerprint: str,
        exchange_account_fingerprint: str | None,
        permissions_json: dict[str, Any],
        validation_status: str,
        is_active: bool = True,
    ) -> dict[str, Any]:
        query = """
        insert into user_api_keys (
            user_id,
            exchange,
            label,
            encrypted_api_key,
            encrypted_api_secret,
            encrypted_passphrase,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at
        )
        values (
            $1, $2, $3, $4, $5, $6, $7, $8,
            $9::jsonb,
            now(),
            $10,
            $11,
            case when $10 = 'valid' then now() else null end
        )
        on conflict (id) do nothing
        returning
            id,
            user_id,
            exchange,
            label,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at,
            created_at,
            updated_at
        """
        permissions_payload = self._jsonb_payload(permissions_json)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                user_id,
                exchange,
                label,
                encrypted_api_key,
                encrypted_api_secret,
                encrypted_passphrase,
                api_key_fingerprint,
                exchange_account_fingerprint,
                permissions_payload,
                validation_status,
                is_active,
            )
        if row:
            return dict(row)

        # fallback path: update active key with same user+exchange+label
        update_query = """
        update user_api_keys
        set
            encrypted_api_key = $4,
            encrypted_api_secret = $5,
            encrypted_passphrase = $6,
            api_key_fingerprint = $7,
            exchange_account_fingerprint = $8,
            permissions_json = $9::jsonb,
            permissions_checked_at = now(),
            validation_status = $10,
            is_active = $11,
            last_validated_at = case when $10 = 'valid' then now() else null end,
            updated_at = now()
        where
            user_id = $1
            and exchange = $2
            and label = $3
        returning
            id,
            user_id,
            exchange,
            label,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                update_query,
                user_id,
                exchange,
                label,
                encrypted_api_key,
                encrypted_api_secret,
                encrypted_passphrase,
                api_key_fingerprint,
                exchange_account_fingerprint,
                permissions_payload,
                validation_status,
                is_active,
            )
        if row:
            return dict(row)

        insert_fallback_query = """
        insert into user_api_keys (
            user_id,
            exchange,
            label,
            encrypted_api_key,
            encrypted_api_secret,
            encrypted_passphrase,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at
        )
        values (
            $1, $2, $3, $4, $5, $6, $7, $8,
            $9::jsonb,
            now(),
            $10,
            $11,
            case when $10 = 'valid' then now() else null end
        )
        returning
            id,
            user_id,
            exchange,
            label,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                insert_fallback_query,
                user_id,
                exchange,
                label,
                encrypted_api_key,
                encrypted_api_secret,
                encrypted_passphrase,
                api_key_fingerprint,
                exchange_account_fingerprint,
                permissions_payload,
                validation_status,
                is_active,
            )
        assert row is not None
        return dict(row)

    async def mark_key_invalid(
        self,
        *,
        key_id,
        permissions_json: dict[str, Any] | None = None,
    ) -> None:
        query = """
        update user_api_keys
        set
            permissions_json = coalesce($2::jsonb, permissions_json),
            permissions_checked_at = now(),
            validation_status = 'invalid',
            is_active = false,
            updated_at = now()
        where id = $1
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, key_id, self._jsonb_payload(permissions_json))

    async def deactivate_other_keys_for_user(
        self,
        *,
        user_id,
        exchange: str,
        except_key_id=None,
    ) -> None:
        if except_key_id is None:
            query = """
            update user_api_keys
            set
                is_active = false,
                updated_at = now()
            where user_id = $1
              and exchange = $2
            """
            args = (user_id, exchange)
        else:
            query = """
            update user_api_keys
            set
                is_active = false,
                updated_at = now()
            where user_id = $1
              and exchange = $2
              and id <> $3
            """
            args = (user_id, exchange, except_key_id)

        async with self.pool.acquire() as conn:
            await conn.execute(query, *args)

    async def get_latest_key_by_runtime_user_id(
        self,
        *,
        runtime_user_id: str,
        exchange: str = "binance",
    ) -> dict[str, Any] | None:
        if not runtime_user_id.startswith("tg:"):
            return None

        telegram_id_raw = runtime_user_id.removeprefix("tg:")
        if not telegram_id_raw.isdigit():
            return None

        telegram_id = int(telegram_id_raw)

        query = """
        select
            u.id as user_uuid,
            u.email,
            u.display_name,
            tp.telegram_id,
            k.id,
            k.user_id,
            k.exchange,
            k.label,
            k.api_key_fingerprint,
            k.exchange_account_fingerprint,
            k.permissions_json,
            k.permissions_checked_at,
            k.validation_status,
            k.is_active,
            k.last_validated_at,
            k.created_at,
            k.updated_at
        from telegram_profiles tp
        join users u
            on u.id = tp.user_id
        join user_api_keys k
            on k.user_id = u.id
        where
            tp.telegram_id = $1
            and k.exchange = $2
        order by
            k.is_active desc,
            k.last_validated_at desc nulls last,
            k.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, telegram_id, exchange)
        return dict(row) if row else None

    async def get_latest_key_material_by_runtime_user_id(
        self,
        *,
        runtime_user_id: str,
        exchange: str = "binance",
        require_active: bool = False,
    ) -> dict[str, Any] | None:
        if not runtime_user_id.startswith("tg:"):
            return None

        telegram_id_raw = runtime_user_id.removeprefix("tg:")
        if not telegram_id_raw.isdigit():
            return None

        telegram_id = int(telegram_id_raw)

        active_filter = "and k.is_active = true" if require_active else ""

        query = f"""
        select
            u.id as user_uuid,
            u.email,
            u.display_name,
            tp.telegram_id,
            tp.chat_id,
            tp.username,
            k.id,
            k.user_id,
            k.exchange,
            k.label,
            k.encrypted_api_key,
            k.encrypted_api_secret,
            k.encrypted_passphrase,
            k.api_key_fingerprint,
            k.exchange_account_fingerprint,
            k.permissions_json,
            k.permissions_checked_at,
            k.validation_status,
            k.is_active,
            k.last_validated_at,
            k.created_at,
            k.updated_at
        from telegram_profiles tp
        join users u
            on u.id = tp.user_id
        join user_api_keys k
            on k.user_id = u.id
        where
            tp.telegram_id = $1
            and k.exchange = $2
            {active_filter}
        order by
            k.is_active desc,
            k.last_validated_at desc nulls last,
            k.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, telegram_id, exchange)
        return dict(row) if row else None

    async def get_latest_key_by_user_id(
        self,
        *,
        user_id,
        exchange: str = "binance",
    ) -> dict[str, Any] | None:
        query = """
        select
            u.id as user_uuid,
            u.email,
            u.display_name,
            tp.telegram_id,
            tp.chat_id,
            tp.username,
            k.id,
            k.user_id,
            k.exchange,
            k.label,
            k.api_key_fingerprint,
            k.exchange_account_fingerprint,
            k.permissions_json,
            k.permissions_checked_at,
            k.validation_status,
            k.is_active,
            k.last_validated_at,
            k.created_at,
            k.updated_at
        from users u
        left join telegram_profiles tp
            on tp.user_id = u.id
        join user_api_keys k
            on k.user_id = u.id
        where u.id = $1
          and k.exchange = $2
        order by
            k.is_active desc,
            k.last_validated_at desc nulls last,
            k.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, exchange)
        return dict(row) if row else None

    async def get_latest_key_material_by_user_id(
        self,
        *,
        user_id,
        exchange: str = "binance",
        require_active: bool = False,
    ) -> dict[str, Any] | None:
        active_filter = "and k.is_active = true" if require_active else ""

        query = f"""
        select
            u.id as user_uuid,
            u.email,
            u.display_name,
            tp.telegram_id,
            tp.chat_id,
            tp.username,
            k.id,
            k.user_id,
            k.exchange,
            k.label,
            k.encrypted_api_key,
            k.encrypted_api_secret,
            k.encrypted_passphrase,
            k.api_key_fingerprint,
            k.exchange_account_fingerprint,
            k.permissions_json,
            k.permissions_checked_at,
            k.validation_status,
            k.is_active,
            k.last_validated_at,
            k.created_at,
            k.updated_at
        from users u
        left join telegram_profiles tp
            on tp.user_id = u.id
        join user_api_keys k
            on k.user_id = u.id
        where u.id = $1
          and k.exchange = $2
          {active_filter}
        order by
            k.is_active desc,
            k.last_validated_at desc nulls last,
            k.created_at desc
        limit 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, user_id, exchange)
        return dict(row) if row else None

    async def update_validation_result(
        self,
        *,
        key_id,
        permissions_json: dict[str, Any],
        validation_status: str,
        is_active: bool,
        exchange_account_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        query = """
        update user_api_keys
        set
            permissions_json = $2::jsonb,
            permissions_checked_at = now(),
            validation_status = $3,
            is_active = $4,
            exchange_account_fingerprint = coalesce($5, exchange_account_fingerprint),
            last_validated_at = case when $3 = 'valid' then now() else last_validated_at end,
            updated_at = now()
        where id = $1
        returning
            id,
            user_id,
            exchange,
            label,
            api_key_fingerprint,
            exchange_account_fingerprint,
            permissions_json,
            permissions_checked_at,
            validation_status,
            is_active,
            last_validated_at,
            created_at,
            updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                key_id,
                self._jsonb_payload(permissions_json),
                validation_status,
                is_active,
                exchange_account_fingerprint,
            )
        return dict(row) if row else None

    async def list_invalid_users(self, *, exchange: str = "binance", limit: int = 50) -> list[dict[str, Any]]:
        query = """
        select
            u.id as user_uuid,
            u.email,
            u.display_name,
            tp.telegram_id,
            tp.username,
            k.id as key_id,
            k.label,
            k.validation_status,
            k.is_active,
            k.permissions_json,
            k.permissions_checked_at,
            k.last_validated_at,
            k.updated_at
        from user_api_keys k
        join users u
            on u.id = k.user_id
        left join telegram_profiles tp
            on tp.user_id = u.id
        where
            k.exchange = $1
            and k.validation_status <> 'valid'
        order by k.updated_at desc
        limit $2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, exchange, int(limit))
        return [dict(row) for row in rows]
