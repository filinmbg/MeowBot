from __future__ import annotations

from typing import Any
from time import time


class TelegramUsersRepositoryMongoAsync:
    def __init__(self, db):
        self.col = db["telegram_users"]

    async def get_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        return await self.col.find_one({"telegram_id": int(telegram_id)})

    async def get_by_trading_user_id(self, trading_user_id: str) -> dict[str, Any] | None:
        return await self.col.find_one({"trading_user_id": str(trading_user_id)})

    async def upsert_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        chat_id: int,
        telegram_language_code: str | None,
        preferred_language: str | None,
    ) -> dict[str, Any]:
        now = int(time() * 1000)
        default_trading_user_id = f"tg:{telegram_id}"

        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {
                "$set": {
                    "telegram_id": int(telegram_id),
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "chat_id": int(chat_id),
                    "telegram_language_code": telegram_language_code,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "preferred_language": preferred_language,
                    "is_onboarded": False,
                    "trading_user_id": default_trading_user_id,
                    "bot_enabled": True,
                    "trading_mode": "sandbox",
                    "notifications_enabled": True,
                    "default_stake_mode": "percent",
                    "default_stake_value": 1.0,
                    "default_leverage": 20,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return await self.col.find_one({"telegram_id": int(telegram_id)})

    async def mark_onboarded(self, telegram_id: int) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"is_onboarded": True}},
            upsert=True,
        )

    async def set_preferred_language(self, telegram_id: int, language: str) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"preferred_language": language}},
            upsert=True,
        )

    async def set_trading_mode(self, telegram_id: int, mode: str) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"trading_mode": mode}},
            upsert=True,
        )

    async def set_notifications_enabled(self, telegram_id: int, enabled: bool) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"notifications_enabled": bool(enabled)}},
            upsert=True,
        )

    async def set_default_stake_value(self, telegram_id: int, value: float) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"default_stake_mode": "percent", "default_stake_value": float(value)}},
            upsert=True,
        )

    async def set_default_leverage(self, telegram_id: int, value: int) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"default_leverage": int(value)}},
            upsert=True,
        )

    async def set_bot_enabled(self, telegram_id: int, enabled: bool) -> None:
        await self.col.update_one(
            {"telegram_id": int(telegram_id)},
            {"$set": {"bot_enabled": bool(enabled)}},
            upsert=True,
        )

    async def get_settings_snapshot(self, telegram_id: int) -> dict[str, Any]:
        row = await self.col.find_one({"telegram_id": int(telegram_id)})
        return row or {}

    async def list_enabled_trading_users(self) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {
                "is_onboarded": True,
                "bot_enabled": True,
            }
        ).sort("created_at", 1).limit(1000)
        return await cursor.to_list(length=1000)

    async def resolve_notification_target_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        row = await self.get_by_trading_user_id(user_id)
        if not row:
            return None

        return {
            "telegram_id": row.get("telegram_id"),
            "chat_id": row.get("chat_id"),
            "email": row.get("email"),
            "preferred_language": row.get("preferred_language", "uk"),
            "notifications_enabled": bool(row.get("notifications_enabled", True)),
            "bot_enabled": bool(row.get("bot_enabled", True)),
            "username": row.get("username"),
            "first_name": row.get("first_name"),
        }
