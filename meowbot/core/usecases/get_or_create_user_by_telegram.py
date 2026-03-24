from __future__ import annotations

from typing import Any

from meowbot.infra.postgres.repos.telegram_auth_repo import TelegramAuthRepo
from meowbot.infra.postgres.repos.user_defaults_repo import UserDefaultsRepo


class GetOrCreateUserByTelegramUseCase:
    def __init__(
        self,
        telegram_repo: TelegramAuthRepo,
        defaults_repo: UserDefaultsRepo,
    ):
        self.telegram_repo = telegram_repo
        self.defaults_repo = defaults_repo

    async def execute(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> tuple[dict[str, Any], bool]:
        existing = await self.telegram_repo.get_user_by_telegram_user_id(telegram_user_id)
        if existing:
            await self.telegram_repo.update_telegram_profile(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            refreshed = await self.telegram_repo.get_user_by_telegram_user_id(telegram_user_id)
            assert refreshed is not None
            return refreshed, False

        created = await self.telegram_repo.create_user_with_telegram(
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
        )

        await self.defaults_repo.create_defaults_for_user(created["id"])

        return created, True