from __future__ import annotations

from typing import Any


class RegisterTelegramUserUseCase:
    def __init__(
        self,
        *,
        users_repo,
        telegram_profiles_repo,
        trader_settings_repo,
        notification_preferences_repo,
        subscription_plans_repo,
        user_subscriptions_repo,
    ) -> None:
        self.users_repo = users_repo
        self.telegram_profiles_repo = telegram_profiles_repo
        self.trader_settings_repo = trader_settings_repo
        self.notification_preferences_repo = notification_preferences_repo
        self.subscription_plans_repo = subscription_plans_repo
        self.user_subscriptions_repo = user_subscriptions_repo

    async def execute(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        chat_id: int,
        language: str,
    ) -> dict[str, Any]:
        existing = await self.users_repo.get_by_telegram_id(int(telegram_id))
        if existing:
            await self.telegram_profiles_repo.touch_last_seen(int(telegram_id))
            return existing

        display_name = self._build_display_name(
            first_name=first_name,
            last_name=last_name,
            username=username,
        )

        user = await self.users_repo.create(
            display_name=display_name,
            preferred_language=language,
            role="user",
            status="active",
        )

        await self.telegram_profiles_repo.upsert_profile(
            user_id=user["id"],
            telegram_id=int(telegram_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            chat_id=int(chat_id),
            is_onboarded=False,
        )

        await self.trader_settings_repo.create_default(user["id"])
        await self.notification_preferences_repo.create_default(user["id"])

        active_sub = await self.user_subscriptions_repo.get_active_by_user_id(user["id"])
        if active_sub is None:
            free_plan = await self.subscription_plans_repo.get_by_code("free")
            if free_plan is None:
                raise RuntimeError("FREE subscription plan not found in Postgres")
            await self.user_subscriptions_repo.create_free_subscription(
                user_id=user["id"],
                plan_id=free_plan["id"],
            )

        created = await self.users_repo.get_by_telegram_id(int(telegram_id))
        if created is None:
            raise RuntimeError("User was created but could not be reloaded from Postgres")

        return created

    @staticmethod
    def _build_display_name(
        *,
        first_name: str | None,
        last_name: str | None,
        username: str | None,
    ) -> str:
        full_name = " ".join(x for x in [first_name, last_name] if x).strip()
        if full_name:
            return full_name
        if username:
            return username
        return "Telegram User"