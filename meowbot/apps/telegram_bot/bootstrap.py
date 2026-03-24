from __future__ import annotations

from meowbot.core.usecases.get_or_create_user_by_telegram import GetOrCreateUserByTelegramUseCase
from meowbot.infra.postgres.repos.telegram_auth_repo import TelegramAuthRepo
from meowbot.infra.postgres.repos.user_defaults_repo import UserDefaultsRepo


def build_get_or_create_user_by_telegram_usecase(pg_pool) -> GetOrCreateUserByTelegramUseCase:
    telegram_repo = TelegramAuthRepo(pg_pool)
    defaults_repo = UserDefaultsRepo(pg_pool)
    return GetOrCreateUserByTelegramUseCase(
        telegram_repo=telegram_repo,
        defaults_repo=defaults_repo,
    )